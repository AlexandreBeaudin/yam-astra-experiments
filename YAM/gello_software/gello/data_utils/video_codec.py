"""Per-camera H.264 contexts in an isolated, bounded-lifetime encoder process."""

import multiprocessing as mp
from fractions import Fraction


def _encode_worker(connection):
    try:
        import av

        av.CodecContext.create("libx264", "w")
        connection.send((True, None))
        contexts = {}
        while True:
            request = connection.recv()
            if request is None:
                for codec, _, _ in contexts.values():
                    if codec.encode(None):
                        raise RuntimeError("unexpected delayed H.264 frames")
                connection.send((True, None))
                break
            topic, pixels, stamp = request
            if pixels.ndim != 3 or pixels.shape[2] != 3:
                raise ValueError("H.264 requires an RGB image")
            height, width, _ = pixels.shape
            if width % 2 or height % 2:
                raise ValueError("H.264 yuv420p requires even image dimensions")
            if topic not in contexts:
                codec = av.CodecContext.create("libx264", "w")
                codec.width, codec.height = width, height
                codec.pix_fmt = "yuv420p"
                codec.time_base = Fraction(1, 1_000_000_000)
                codec.framerate = Fraction(30, 1)
                codec.thread_count = 2
                codec.options = {
                    "crf": "18",
                    "preset": "veryfast",
                    "tune": "zerolatency",
                    "bf": "0",
                    "g": "30",
                    "x264-params": "repeat-headers=1:annexb=1",
                }
                codec.open()
                contexts[topic] = (codec, stamp, -1)
            codec, origin, previous = contexts[topic]
            pts = stamp - origin
            if pts <= previous or (width, height) != (codec.width, codec.height):
                raise ValueError("H.264 source timestamp or resolution changed")
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts, frame.time_base = pts, codec.time_base
            packets = codec.encode(frame)
            if len(packets) != 1 or packets[0].pts != pts:
                raise RuntimeError("H.264 encoder delayed or reordered a frame")
            contexts[topic] = (codec, origin, pts)
            packet = packets[0]
            connection.send((True, (bytes(packet), packet.is_keyframe)))
    except Exception as exc:
        try:
            connection.send((False, f"{type(exc).__name__}: {exc}"))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


class H264Encoder:
    def __init__(self):
        ctx = mp.get_context("spawn")
        self.connection, child = ctx.Pipe()
        self.process = ctx.Process(target=_encode_worker, args=(child,), daemon=True)
        self.process.start()
        child.close()
        try:
            self._receive(10)
        except Exception:
            self.close()
            raise

    def _receive(self, timeout=2):
        if not self.connection.poll(timeout):
            raise TimeoutError("H.264 encoder timeout")
        ok, result = self.connection.recv()
        if not ok:
            raise RuntimeError(f"H.264 encoder failed: {result}")
        return result

    def encode(self, topic, pixels, stamp):
        self.connection.send((topic, pixels, stamp))
        return self._receive()

    def finish(self):
        self.connection.send(None)
        self._receive()

    def close(self):
        self.connection.close()
        self.process.join(timeout=0.2)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=2)


class ImageDecoder:
    """Decode every access unit in order, including frames skipped by export alignment."""

    def __init__(self):
        self.contexts = {}

    def decode(self, topic, data):
        import base64

        import av

        codec = self.contexts.get(topic)
        if codec is None:
            codec = av.CodecContext.create("h264", "r")
            codec.thread_count = 1
            self.contexts[topic] = codec
        frames = codec.decode(av.Packet(base64.b64decode(data["data"], validate=True)))
        if len(frames) != 1:
            raise ValueError("invalid or delayed H.264 frame")
        pixels = frames[0].to_ndarray(format="rgb24")
        if list(pixels.shape) != data["shape"]:
            raise ValueError("decoded H.264 dimensions mismatch")
        return pixels
