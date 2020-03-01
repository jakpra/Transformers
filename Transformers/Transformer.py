from Transformers.utils import *


class EncoderLayer(nn.Module):
    def __init__(self, num_heads: int, d_model: int, d_k: int, d_v: int, d_intermediate: int, dropout: float) -> None:
        super(EncoderLayer, self).__init__()
        self.dropout_rate = dropout
        self.mha = MultiHeadAttention(num_heads, d_model, d_k, d_v)
        self.ffn = FFN(d_intermediate, d_model)
        self.ln_mha = nn.LayerNorm(normalized_shape=d_model)
        self.ln_ffn = nn.LayerNorm(normalized_shape=d_model)

    def forward(self, x: EncoderInput) -> EncoderInput:
        n_in = x.encoder_input.shape[1]
        x_drop = F.dropout(x.encoder_input, p=self.dropout_rate, training=self.training)
        mha_x = self.mha(x_drop, x_drop, x_drop, x.mask[:, :n_in])
        mha_x = F.dropout(mha_x, p=self.dropout_rate, training=self.training)
        mha_x = mha_x + x_drop
        mha_x = self.ln_mha(mha_x)

        ffn_x = self.ffn(mha_x)
        ffn_x = F.dropout(ffn_x, p=self.dropout_rate, training=self.training)
        ffn_x = ffn_x + mha_x
        ffn_x = self.ln_ffn(ffn_x)
        return EncoderInput(encoder_input=ffn_x, mask=x.mask)


def Encoder(num_layers: int, num_heads: int, d_model: int, d_k: int, d_v: int, d_intermediate: int, dropout: float=0.1)\
        -> nn.Sequential:
    layers = [EncoderLayer(num_heads, d_model, d_k, d_v, d_intermediate, dropout) for _
              in range(num_layers)]
    return nn.Sequential(*layers)


class DecoderLayer(nn.Module):
    def __init__(self, num_heads: int, d_model: int, d_k: int, d_v: int, d_intermediate: int, dropout: float) \
            -> None:
        super(DecoderLayer, self).__init__()
        self.dropout_rate = dropout
        self.mask_mha = MultiHeadAttention(num_heads, d_model, d_k, d_v)
        self.mha = MultiHeadAttention(num_heads, d_model, d_k, d_v)
        self.ffn = FFN(d_intermediate, d_model)
        self.ln_m_mha = nn.LayerNorm(normalized_shape=d_model)
        self.ln_mha = nn.LayerNorm(normalized_shape=d_model)
        self.ln_ffn = nn.LayerNorm(normalized_shape=d_model)

    def forward(self, x: DecoderInput) -> DecoderInput:
        t = x.decoder_input.shape[1]
        x_drop = F.dropout(x.decoder_input, p=self.dropout_rate, training=self.training)
        m_mha_x = self.mask_mha(x_drop, x_drop, x_drop, x.decoder_mask)
        m_mha_x = F.dropout(m_mha_x, p=self.dropout_rate, training=self.training)
        m_mha_x = m_mha_x + x_drop
        m_mha_x = self.ln_m_mha(m_mha_x)

        mha_x = self.mha(m_mha_x, x.encoder_output, x.encoder_output, x.encoder_mask[:, :t, :])
        mha_x = F.dropout(mha_x, p=self.dropout_rate, training=self.training)
        mha_x = mha_x + m_mha_x
        mha_x = self.ln_mha(mha_x)

        ffn_x = self.ffn(mha_x)
        ffn_x = F.dropout(ffn_x, p=self.dropout_rate, training=self.training)
        ffn_x = ffn_x + mha_x
        ffn_x = self.ln_ffn(ffn_x)

        return DecoderInput(encoder_output=x.encoder_output,
                            decoder_input=ffn_x,
                            decoder_mask=x.decoder_mask,
                            encoder_mask=x.encoder_mask)


def Decoder(num_layers: int, num_heads: int, d_model: int, d_k: int, d_v: int, d_intermediate: int,
            dropout: float = 0.1) -> nn.Sequential:
    return nn.Sequential(*[DecoderLayer(num_heads, d_model, d_k, d_v, d_intermediate, dropout)
                           for _ in range(num_layers)])


class Transformer(nn.Module):
    def __init__(self, num_classes: int, encoder_heads: int = 8, decoder_heads: int = 8, encoder_layers: int = 6,
                 decoder_layers: int = 6, d_model: int = 300, d_intermediate: int = 128, dropout: float = 0.1,
                 device: str = 'cpu', activation: Callable[[Tensor], Tensor] = sigsoftmax,
                 reuse_embedding: bool = True, predictor: Optional[nn.Module] = None, padding_index=0) -> None:
        self.device = device
        super(Transformer, self).__init__()
        self.encoder = Encoder(num_layers=encoder_layers, num_heads=encoder_heads, d_model=d_model,
                               d_k=d_model // encoder_heads, d_v=d_model // encoder_heads,
                               d_intermediate=d_intermediate, dropout=dropout).to(self.device)
        self.decoder = Decoder(num_layers=decoder_layers, num_heads=decoder_heads, d_model=d_model,
                               d_k=d_model // decoder_heads, d_v=d_model // decoder_heads,
                               d_intermediate=d_intermediate, dropout=dropout).to(self.device)
        self.embedding_matrix = torch.nn.Parameter(torch.rand(num_classes, d_model, device=device) * 0.02)
        # print(self.embedding_matrix.size())
        self.output_embedder = lambda x: F.embedding(x, self.embedding_matrix, padding_idx=padding_index, scale_grad_by_freq=True)
        if reuse_embedding:
            self.predictor = lambda x: x@(self.embedding_matrix.transpose(1, 0) + 1e-10)
        elif predictor is not None:
            self.predictor = predictor
        else:
            self.predictor = nn.Linear(in_features=d_model, out_features=num_classes).to(self.device)

        self.activation = activation

    def forward(self, encoder_input: Tensor, decoder_input: Tensor, encoder_mask: LongTensor,
                decoder_mask: LongTensor) -> Tensor:
        self.train()

        b, n, dk = encoder_input.shape
        n_out = decoder_input.shape[1]
        pe = PE(b, n, dk, dk, device=self.device)
        pe_dec = PE(b, n_out, dk, dk, device=self.device)
        encoder_output = self.encoder(EncoderInput(encoder_input + pe, encoder_mask[:, :n, :]))
        decoder_output = self.decoder(DecoderInput(encoder_output=encoder_output.encoder_input,
                                                   encoder_mask=encoder_mask, decoder_input=decoder_input + pe_dec,
                                                   decoder_mask=decoder_mask))
        prediction = self.predictor(decoder_output.decoder_input)[:, :, :-1]  # never predict START
        return torch.log(self.activation(prediction))

    def infer(self, encoder_input: Tensor, encoder_mask: LongTensor, sos_symbol: int, sep_symbol: int, seq_lens: Tensor) -> Tensor:
        self.eval()

        with torch.no_grad():
            b, n, dk = encoder_input.shape
            max_steps = encoder_mask.shape[1]
            pe = PE(b, max_steps, dk, dk, device=self.device)
            encoder_output = self.encoder(EncoderInput(encoder_input + pe[:, :n], encoder_mask[:, :n, :])).encoder_input
            sos_symbols = (torch.ones(b) * sos_symbol).long().to(self.device)
            decoder_output = self.output_embedder(sos_symbols).unsqueeze(1) + pe[:, 0:1, :]
            output_probs = torch.Tensor().to(self.device)
            inferer = infer_wrapper(self, encoder_output, encoder_mask, b)
            decoder_mask = make_mask((b, encoder_mask.shape[1], encoder_mask.shape[1])).to(self.device)

            seps_predicted = torch.zeros(encoder_mask.shape[0], dtype=torch.long)

            for t in range(max_steps):
                prob_t = inferer(decoder_output=decoder_output, t=t, decoder_mask=decoder_mask)
                class_t = prob_t.argmax(dim=-1)
                emb_t = self.output_embedder(class_t).unsqueeze(1) + pe[:, t + 1:t + 2, :]
                decoder_output = torch.cat([decoder_output, emb_t], dim=1)
                output_probs = torch.cat([output_probs, prob_t.unsqueeze(1)], dim=1)

                seps_predicted = seps_predicted + (prob_t[:, :-1].argmax(dim=-1) == sep_symbol).long()
                if torch.all(seps_predicted >= seq_lens):
                    break

        return output_probs

    def infer_one(self, encoder_output: Tensor, encoder_mask: LongTensor, decoder_output: Tensor,
                  t: int, b: int, decoder_mask: Optional[LongTensor] = None) -> Tensor:
        if decoder_mask is None:
            decoder_mask = make_mask((b, t + 1, t + 1)).to(self.device)
        decoder_step = self.decoder(DecoderInput(encoder_output=encoder_output, encoder_mask=encoder_mask,
                                                 decoder_input=decoder_output,
                                                 decoder_mask=decoder_mask[:, :t+1, :t+1])).decoder_input
        prob_t = self.predictor(decoder_step[:, -1])[:, :-1]
        return self.activation(prob_t)  # b, num_classes

    def vectorized_beam_search(self, encoder_input: Tensor, encoder_mask: LongTensor, sos_symbol: int,
                               beam_width: int):
        self.eval()

        def forward_index(dim1: int, dim2: int) -> int:
            return dim1 * beam_width + dim2

        def backward_index(idx: int) -> Tuple[int, int]:
            return idx // beam_width, idx - (idx // beam_width) * beam_width

        with torch.no_grad():
            b, n, dk = encoder_input.shape
            pe = PE(b, n, dk, dk, device=self.device)

            encoder_output = self.encoder(EncoderInput(encoder_input + pe, encoder_mask[:, :n, :])).encoder_input
            sos_symbols = (torch.ones(b, device=self.device) * sos_symbol).long()
            # tensor of shape B, 1, F
            decoder_output = self.output_embedder(sos_symbols).unsqueeze(1) + pe[:, 0:1]

            decoder_mask = make_mask((b, encoder_mask.shape[1], encoder_mask.shape[1])).to(self.device)

            # construct first inferer for single batch
            inferer = infer_wrapper(self, encoder_output, encoder_mask, b)

            # first branching
            # get first outer probabilities
            probs_0 = inferer(decoder_output=decoder_output, t=0, decoder_mask=decoder_mask)

            # pick best K of them
            outer_beam_scores, outer_beam_paths = torch.topk(probs_0, k=beam_width, dim=-1)
            # embed them, concatenate with sos symbols and reshape for batching
            outer_beam_decoder_outputs = torch.cat((decoder_output.repeat(beam_width, 1, 1),
                                                    self.output_embedder(outer_beam_paths).view(beam_width*b, 1, dk) +
                                                    pe[:, 1:2].repeat(beam_width, 1, 1)),
                                                   dim=1)

            outer_beam_scores = torch.log(outer_beam_scores)

            # expand the paths for later
            outer_beam_paths = outer_beam_paths.unsqueeze(-1)
            # construct a new inferer for batched beams
            inferer = infer_wrapper(self, encoder_output.repeat(beam_width, 1, 1),
                                    encoder_mask.repeat(beam_width, 1, 1), b * beam_width)

            decoder_mask = decoder_mask.repeat(beam_width, 1, 1)

            for t in range(1, n-1):
                # tensor of shape K, B, N
                probs_t = inferer(decoder_output=outer_beam_decoder_outputs, t=t, decoder_mask=decoder_mask).\
                    view(beam_width, b, -1)

                # list of K tuples, each containing scores and indices
                per_beam_top_k = [torch.topk(probs_t[i], k=beam_width, dim=-1) for i in range(beam_width)]

                # tensor of shape K0, K1, B, where K0 indexes the source and K1 indexes the gen
                per_beam_scores = torch.cat([x[0].unsqueeze(0) for x in per_beam_top_k])
                per_beam_scores = torch.log(per_beam_scores)
                per_beam_paths = torch.cat([x[1].unsqueeze(0) for x in per_beam_top_k])

                # tensor of shape K, K, B
                per_beam_scores = per_beam_scores.transpose(1, 2)
                masked_sentences = (encoder_mask[:, t + 1, t + 1] == 0).repeat(beam_width, beam_width, 1)  # TODO: .repeat(beam_width, 1, beam_width)
                per_beam_scores[masked_sentences] = 0.

                outer_beam_scores = outer_beam_scores.unsqueeze(1).expand(beam_width, beam_width, b)
                per_beam_scores = per_beam_scores + outer_beam_scores

                # tensor of shape K^2, B -> B, K^2
                per_beam_scores = per_beam_scores.view(beam_width ** 2, b).transpose(1, 0)
                # tensors of shape K, B
                outer_beam_scores, outer_beam_indices = torch.topk(per_beam_scores, k=beam_width, dim=-1)

                square_indices = [list(map(backward_index, x)) for x in outer_beam_indices.tolist()]

                # tensor of shape K, B, t+2, F
                new_outer_beam_decoder_outputs = torch.zeros(beam_width, b, t + 2, dk,
                                                             device=self.device, dtype=torch.float)
                # update the paths and embeddings
                new_outer_beam_paths = torch.tensor([], dtype=torch.long, device=self.device)
                outer_beam_decoder_outputs = outer_beam_decoder_outputs.view(beam_width, b, t+1, dk)
                for i, new_best in enumerate(square_indices):
                    this_beam_path = torch.tensor([], dtype=torch.long, device=self.device)
                    for s, (k_outer, k_inner) in enumerate(new_best):
                        this_sentence_history = outer_beam_paths[k_outer][s:s+1].squeeze(0)
                        this_sentence_future = per_beam_paths[k_outer, k_inner, s:s+1]
                        this_sentence_path = torch.cat((this_sentence_history, this_sentence_future))
                        this_beam_path = torch.cat((this_beam_path, this_sentence_path.unsqueeze(0)), dim=0)
                        new_outer_beam_decoder_outputs[i, s, :t+1] = outer_beam_decoder_outputs[k_outer][s]

                    new_outer_beam_paths = torch.cat((new_outer_beam_paths, this_beam_path.unsqueeze(0)), dim=0)

                new_outer_beam_decoder_outputs[:, :, t + 1] = \
                    self.output_embedder(new_outer_beam_paths[:, :, -1]) + pe[:, t+1]

                outer_beam_paths = new_outer_beam_paths
                outer_beam_decoder_outputs = new_outer_beam_decoder_outputs.view(beam_width * b, t + 2, dk)
        return outer_beam_paths, outer_beam_scores


def test(device: str):
    sl = 25
    nc = 1000

    t = Transformer(12, device=device)
    encoder_input = torch.rand(128, sl, 300).to(device)
    encoder_mask = torch.ones(128, sl*2, sl).to(device)
    decoder_input = torch.rand(128, sl*2, 300).to(device)
    decoder_mask = make_mask((128, sl * 2, sl * 2)).to(device)
    p, s = t.vectorized_beam_search(encoder_input[0:20], encoder_mask[0:20], 0, 3)
    f_v = t.forward(encoder_input, decoder_input, encoder_mask, decoder_mask)
    i_v = t.infer(encoder_input[0:20], encoder_mask[0:20, :50], 0)
    import pdb
    pdb.set_trace()