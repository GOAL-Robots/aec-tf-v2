from omegaconf import OmegaConf


OmegaConf.register_resolver("decoder_out_size", lambda a,b,c: int(a) * int(b) * int(c))
OmegaConf.register_resolver("slash_to_dot", lambda s: s.replace("/", "."))
