"""Subject-silhouette extraction, run as a child process of the GUI.

Everything heavy about the `silhouette` transition lives here, outside the
selector: onnxruntime, the ~170 MB segmentation model and the numpy passes that
turn its mask into the field texture the shader samples. The GUI only spawns
this script (see `core/silhouette.py`) and reads one status line per request,
so a selector that never uses the transition pays nothing for it, and a crash
or an out-of-memory in here cannot take the overlay down.

Deliberately a plain script with no imports from the package: it is started by
file path with whatever interpreter runs the GUI.

Protocol (tab-separated, one message per line):
  stdin   <source image>\t<destination png>
  stdout  ok|none|fail\t<source image>      per request
          model\tdownloading|ready|failed   once, while starting up

`none` means the model found no usable subject; the destination then holds a
1x1 PNG, a marker that keeps the same image from being segmented again.

The field texture is RGB, at the source's aspect ratio:
  R  signed distance to the contour in texels, clamped to +-NEAR_TEXELS and
     mapped to 0..1 (0.5 on the contour, below it inside the subject)
  G  sqrt(|distance| / farthest distance): coarse, but covers the whole image
  B  distance *along* the contour from where its line starts, normalised by the
     longest one, looked up from the nearest contour point
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.error
import urllib.request
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QImageReader

# Must match NEAR_TEXELS in ui/qml/shaders/silhouette.frag.
NEAR_TEXELS = 8.0
# Long side of the field texture. The contour line is reconstructed from a
# blurred distance field, so this only has to out-resolve the mask the model
# returns, not the screen.
_FIELD_EDGE = 960
# A subject smaller than this share of the image is noise, one larger is the
# whole picture; either way there is no silhouette worth drawing.
_MIN_AREA = 0.03
_MAX_AREA = 0.80
# Below this peak the model saw nothing salient at all.
_MIN_PEAK = 0.5
_MASK_BLUR_SIGMA = 1.5
_FIELD_BLUR_SIGMA = 1.0
_DOWNLOAD_TIMEOUT_S = 30
_CHUNK = 1 << 16


@dataclass(frozen=True)
class _Model:
    name: str
    sha256: str
    edge: int
    mean: tuple[float, float, float]
    std: tuple[float, float, float]

    @property
    def filename(self) -> str:
        return f"{self.name}.onnx"

    @property
    def url(self) -> str:
        return f"https://github.com/danielgatis/rembg/releases/download/v0.0.0/{self.filename}"


# In order of preference: the first one already on disk is used, and the first
# entry is what gets downloaded when none is.
_MODELS: tuple[_Model, ...] = (
    # Trained on illustrated characters: tight on them, and reports nothing on
    # anything else, which is what hands those images to _SECOND_OPINION.
    _Model(
        "isnet-anime",
        "f15622d853e8260172812b657053460e20806f04b9e05147d49af7bed31a6e99",
        1024,
        (0.485, 0.456, 0.406),
        (1.0, 1.0, 1.0),
    ),
    _Model(
        "isnet-general-use",
        "60920e99c45464f2ba57bee2ad08c919a52bbf852739e96947fbb4358c0d964a",
        1024,
        (0.5, 0.5, 0.5),
        (1.0, 1.0, 1.0),
    ),
    _Model(
        "u2net",
        "8d10d2f3bb75ae3b6d527c77944fc5e7dcd94b29809d47a739a7a728a912b491",
        320,
        (0.485, 0.456, 0.406),
        (0.229, 0.224, 0.225),
    ),
)


# Asked only about images the preferred model found nothing in: salient objects
# of any kind, with a rougher outline than the character model draws.
_SECOND_OPINION = "isnet-general-use"


def _find_model(
    models_dir: Path, candidates: tuple[_Model, ...] = _MODELS
) -> tuple[_Model, Path] | None:
    # ~/.u2net is where rembg keeps the very same files.
    for directory in (models_dir, Path.home() / ".u2net"):
        for model in candidates:
            path = directory / model.filename
            if path.is_file():
                return model, path
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _download(model: _Model, models_dir: Path) -> Path | None:
    """Fetch `model`, resuming a partial file left by an earlier run.

    The selector is closed at will, which kills this process mid-download; the
    `.part` file is kept so the next launch continues instead of restarting.
    """
    dest = models_dir / model.filename
    part = dest.with_name(dest.name + ".part")
    try:
        models_dir.mkdir(parents=True, exist_ok=True)
        offset = part.stat().st_size if part.exists() else 0
        request = urllib.request.Request(model.url)
        if offset:
            request.add_header("Range", f"bytes={offset}-")
        try:
            response = urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT_S)
        except urllib.error.HTTPError as exc:
            if exc.code != 416:  # 416: the partial file is already complete
                raise
            response = None
        if response is not None:
            with response:
                resumed = response.status == 206
                with part.open("ab" if resumed else "wb") as handle:
                    while chunk := response.read(_CHUNK):
                        handle.write(chunk)
        if _sha256(part) != model.sha256:
            part.unlink(missing_ok=True)
            return None
        part.replace(dest)
    except (OSError, urllib.error.URLError, ValueError):
        return None
    return dest


def _blur(field: np.ndarray, sigma: float) -> np.ndarray:
    """Separable gaussian with edge replication."""
    radius = max(1, round(sigma * 3))
    taps = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2).astype(
        np.float32
    )
    taps /= taps.sum()
    out = field
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (radius, radius)
        padded = np.pad(out, pad, mode="edge")
        acc = np.zeros_like(out)
        length = out.shape[axis]
        for i, weight in enumerate(taps):
            window = [slice(None), slice(None)]
            window[axis] = slice(i, i + length)
            acc += weight * padded[tuple(window)]
        out = acc
    return out


def _to_array(image: QImage, channels: int) -> np.ndarray:
    """Copy a QImage's pixels out as (height, width, channels) uint8."""
    stride = image.bytesPerLine()
    raw = np.frombuffer(
        image.constBits(), dtype=np.uint8, count=stride * image.height()
    )
    rows = raw.reshape(image.height(), stride)[:, : image.width() * channels]
    return rows.reshape(image.height(), image.width(), channels).copy()


def _resize(field: np.ndarray, width: int, height: int) -> np.ndarray:
    """Smooth-scale a 0..1 float field through QImage (no extra dependency)."""
    grey = np.ascontiguousarray((field.clip(0.0, 1.0) * 255.0).astype(np.uint8))
    image = QImage(
        grey.data,
        grey.shape[1],
        grey.shape[0],
        grey.shape[1],
        QImage.Format.Format_Grayscale8,
    )
    scaled = image.scaled(
        width,
        height,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return _to_array(scaled, 1)[:, :, 0].astype(np.float32) / 255.0


def _nearest_seed(seeds: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For every pixel, the coordinates of and distance to its nearest seed.

    Jump flooding: each pass offers every pixel the seed its neighbours at a
    halving stride already know. Wrapping at the borders (np.roll) is harmless,
    since a wrapped neighbour only contributes one more candidate and distances
    are always measured from the candidate's true coordinates.
    """
    height, width = seeds.shape
    ys, xs = np.mgrid[0:height, 0:width].astype(np.int32)
    seed_y = np.where(seeds, ys, -1)
    seed_x = np.where(seeds, xs, -1)
    best = np.where(seeds, 0, np.iinfo(np.int32).max).astype(np.int64)

    stride = 1
    while stride * 2 < max(height, width):
        stride *= 2
    strides = []
    while stride >= 1:
        strides.append(stride)
        stride //= 2
    strides += [2, 1]  # two refinement passes fix jump flooding's rare misses

    for stride in strides:
        for dy in (-stride, 0, stride):
            for dx in (-stride, 0, stride):
                if dy == 0 and dx == 0:
                    continue
                cand_y = np.roll(seed_y, (dy, dx), axis=(0, 1))
                cand_x = np.roll(seed_x, (dy, dx), axis=(0, 1))
                dist = (cand_y - ys).astype(np.int64) ** 2 + (cand_x - xs).astype(
                    np.int64
                ) ** 2
                better = (cand_y >= 0) & (dist < best)
                best = np.where(better, dist, best)
                seed_y = np.where(better, cand_y, seed_y)
                seed_x = np.where(better, cand_x, seed_x)
    return seed_y, seed_x, np.sqrt(best.astype(np.float32))


def _arc_lengths(contour: np.ndarray) -> np.ndarray:
    """Steps along the contour from each closed curve's topmost pixel.

    A breadth-first walk over the contour pixels, so the value is the shorter
    way round: the line is drawn from the top by two pens going opposite ways
    and meeting on the far side. That also makes it continuous everywhere on
    the curve, which matters because the shader samples it with filtering.
    """
    height, width = contour.shape
    steps = np.full(contour.shape, -1, dtype=np.int32)
    for start_y, start_x in np.argwhere(contour):  # row-major: topmost first
        if steps[start_y, start_x] >= 0:
            continue
        steps[start_y, start_x] = 0
        queue = deque([(int(start_y), int(start_x))])
        while queue:
            y, x = queue.popleft()
            reach = steps[y, x] + 1
            for ny in (y - 1, y, y + 1):
                for nx in (x - 1, x, x + 1):
                    if (
                        0 <= ny < height
                        and 0 <= nx < width
                        and contour[ny, nx]
                        and steps[ny, nx] < 0
                    ):
                        steps[ny, nx] = reach
                        queue.append((ny, nx))
    return steps


def _field(mask: np.ndarray) -> np.ndarray | None:
    """Turn a soft subject mask into the RGB field texture, or None."""
    inside = _blur(mask, _MASK_BLUR_SIGMA) > 0.5
    area = float(inside.mean())
    if not _MIN_AREA <= area <= _MAX_AREA:
        return None

    # Contour: subject pixels touching the outside. The image border does not
    # count as outside, so a subject cut off by the frame gets no line along it.
    padded = np.pad(inside, 1, mode="edge")
    surrounded = (
        padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:]
    )
    contour = inside & ~surrounded
    if not contour.any():
        return None

    seed_y, seed_x, dist = _nearest_seed(contour)
    # Contour pixels are the subject's outermost; the edge itself runs half a
    # texel beyond their centres.
    signed = _blur(np.where(inside, -(dist + 0.5), dist - 0.5), _FIELD_BLUR_SIGMA)

    steps = _arc_lengths(contour)
    arc = steps[seed_y, seed_x].astype(np.float32) / max(int(steps.max()), 1)

    near = (0.5 + signed / (2.0 * NEAR_TEXELS)).clip(0.0, 1.0)
    far = np.sqrt(np.abs(signed) / max(float(np.abs(signed).max()), 1.0))
    rgb = np.stack([near, far, arc], axis=-1)
    return np.ascontiguousarray((rgb * 255.0 + 0.5).astype(np.uint8))


class Segmenter:
    def __init__(self, model: _Model, path: Path) -> None:
        options = ort.SessionOptions()
        # Leave the selector's own animations some cores.
        options.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)
        # The arena keeps a run's activations (well over 1 GB) for the life of
        # the session; two models may be loaded at once.
        options.enable_cpu_mem_arena = False
        self._model = model
        self._session = ort.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._input = self._session.get_inputs()[0].name

    def field_for(self, source: Path) -> np.ndarray | None:
        """The field texture for `source`, or None when it has no subject.

        Raises OSError when the image cannot be read.
        """
        reader = QImageReader(str(source))
        reader.setAutoTransform(True)
        size = reader.size()
        if not size.isValid() or size.isEmpty():
            raise OSError(f"cannot read {source}")
        edge = self._model.edge
        # The model takes a square whatever the picture's shape; decoding
        # straight to it never holds the full-resolution image.
        reader.setScaledSize(QSize(edge, edge))
        image = reader.read()
        if image.isNull():
            raise OSError(f"cannot decode {source}")
        if image.size() != QSize(edge, edge):  # a transform swapped the axes
            image = image.scaled(edge, edge)
        pixels = _to_array(image.convertToFormat(QImage.Format.Format_RGB888), 3)

        tensor = pixels.astype(np.float32) / max(float(pixels.max()), 1e-6)
        tensor = (tensor - np.array(self._model.mean, dtype=np.float32)) / np.array(
            self._model.std, dtype=np.float32
        )
        tensor = tensor.transpose(2, 0, 1)[np.newaxis]
        prediction = self._session.run(None, {self._input: tensor})[0][0, 0]

        low, high = float(prediction.min()), float(prediction.max())
        if high < _MIN_PEAK or high - low < 1e-6:
            return None
        mask = (prediction - low) / (high - low)

        if size.width() >= size.height():
            width, height = (
                _FIELD_EDGE,
                max(1, round(_FIELD_EDGE * size.height() / size.width())),
            )
        else:
            width, height = (
                max(1, round(_FIELD_EDGE * size.width() / size.height())),
                _FIELD_EDGE,
            )
        return _field(_resize(mask, width, height))


def _write_png(pixels: np.ndarray, dest: Path) -> None:
    """Atomic write; the tmp name is one core/cachegc.py recognises as stale."""
    height, width, _ = pixels.shape
    image = QImage(pixels.data, width, height, width * 3, QImage.Format.Format_RGB888)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.stem}.tmp.png")
    if not image.save(str(tmp), "PNG"):
        tmp.unlink(missing_ok=True)
        raise OSError(f"cannot write {tmp}")
    tmp.replace(dest)


class SecondOpinionUnavailable(Exception):
    """The second model could not be fetched or loaded."""


class Cascade:
    """The preferred model, then a broader one for what it found nothing in.

    The second model is fetched and loaded on first need, so a library of
    nothing but characters never pays for it.
    """

    def __init__(self, first: Segmenter, second: _Model | None, models_dir: Path):
        self._first = first
        self._second_model = second
        self._second: Segmenter | None = None
        self._models_dir = models_dir

    def _second_opinion(self) -> Segmenter | None:
        if self._second is None and self._second_model is not None:
            model = self._second_model
            found = _find_model(self._models_dir, (model,))
            path = found[1] if found else _download(model, self._models_dir)
            if path is None:
                raise SecondOpinionUnavailable(model.name)
            try:
                self._second = Segmenter(model, path)
            except Exception as exc:
                raise SecondOpinionUnavailable(model.name) from exc
        return self._second

    def field_for(self, source: Path) -> np.ndarray | None:
        """Raises SecondOpinionUnavailable rather than answer "no subject" on
        the first model's word alone: that answer is cached for good."""
        field = self._first.field_for(source)
        if field is None and (second := self._second_opinion()) is not None:
            field = second.field_for(source)
        return field


def process(segmenter: Cascade, source: Path, dest: Path) -> str:
    """Segment one image into `dest`; returns the status word for the protocol."""
    try:
        field = segmenter.field_for(source)
        if field is None:
            _write_png(np.zeros((1, 1, 3), dtype=np.uint8), dest)
            return "none"
        _write_png(field, dest)
        return "ok"
    except Exception:  # noqa: BLE001 - one bad image must not end the session
        return "fail"


def _say(*fields: str) -> None:
    print("\t".join(fields), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", type=Path, required=True, help="model directory")
    parser.add_argument(
        "--once",
        nargs=2,
        metavar=("SRC", "DEST"),
        type=Path,
        help="process one image and exit",
    )
    args = parser.parse_args()

    with suppress(OSError):
        os.nice(10)  # background work: never compete with the selector's frames

    found = _find_model(args.models)
    if found is None:
        _say("model", "downloading")
        path = _download(_MODELS[0], args.models)
        if path is None:
            _say("model", "failed")
            return 1
        found = (_MODELS[0], path)
    try:
        first = Segmenter(*found)
    except Exception:  # noqa: BLE001 - onnxruntime's errors share no useful base
        _say("model", "failed")
        return 1
    second = next(
        (m for m in _MODELS if m.name == _SECOND_OPINION and m != found[0]), None
    )
    segmenter = Cascade(first, second, args.models)
    _say("model", "ready")

    if args.once:
        source, dest = args.once
        _say(process(segmenter, source, dest), str(source))
        return 0
    for line in sys.stdin:
        source, _, dest = line.rstrip("\n").partition("\t")
        if source and dest:
            _say(process(segmenter, Path(source), Path(dest)), source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
