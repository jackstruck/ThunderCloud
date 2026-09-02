# Local model provenance

The ignored ONNX files in this directory were downloaded from the `weights` release
of [`yakhyo/adaface-onnx`](https://github.com/yakhyo/adaface-onnx), whose repository
declares the code and distributed files under the MIT license. They are third-party
ONNX exports, not files published directly by the SCRFD or AdaFace authors.

| Local file | Upstream asset | SHA-256 |
| --- | --- | --- |
| `scrfd.onnx` | `det_10g.onnx` | `5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91` |
| `adaface.onnx` | `adaface_ir_18.onnx` | `6b6a35772fb636cdd4fa86520c1a259d0c41472a76f70f802b351837a00d9870` |

The AdaFace export expects normalized BGR input, so `.env.example` sets
`FACE_EMBEDDING_COLOR_ORDER=BGR`.
