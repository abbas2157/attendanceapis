from dataclasses import dataclass
import logging
import os
import tempfile

import numpy as np
from deepface import DeepFace
from PIL import Image, UnidentifiedImageError


MODEL_NAME = "Facenet"
FACE_MATCH_THRESHOLD = float(os.environ.get("FACE_MATCH_THRESHOLD", "0.78"))
FACE_MATCH_MARGIN = float(os.environ.get("FACE_MATCH_MARGIN", "0.10"))
MAX_IMAGE_BYTES = int(os.environ.get("FACE_IMAGE_MAX_BYTES", str(12 * 1024 * 1024)))
MAX_IMAGE_PIXELS = int(os.environ.get("FACE_IMAGE_MAX_PIXELS", str(25_000_000)))
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

# Prefer the most reliable detector first for attendance verification.
DETECTOR_BACKENDS = ["retinaface", "ssd", "opencv"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FaceMatchResult:
    employee: object | None
    confidence: float
    best_score: float
    second_best_score: float | None
    margin: float | None
    is_match: bool
    is_ambiguous: bool
    decision: str


def iter_employee_embeddings(employee):
    """
    Yield every usable enrollment embedding for an employee.

    Final runtime behavior reads only from EmployeeFaceEmbedding so the sample
    table is the single source of truth for verification.
    """
    face_embeddings = getattr(employee, "face_embeddings", None)
    if face_embeddings is not None:
        try:
            samples = list(face_embeddings.all())
        except Exception:
            samples = []
        for sample in samples:
            # A corrupted stored sample must not break attendance verification
            # for the whole employee set. Skip bad sample rows and continue.
            try:
                sample_embedding = sample.get_embedding()
            except (TypeError, ValueError):
                logger.warning(
                    "Skipping unreadable face sample employee_id=%s sample_id=%s",
                    getattr(employee, "pk", None),
                    getattr(sample, "pk", None),
                )
                continue
            if sample_embedding is not None:
                yield sample_embedding


def extract_embedding_from_path(image_path: str):
    """
    Try multiple detector backends and return the first reliable embedding.
    Production attendance must not create embeddings without face detection.
    """
    for backend in DETECTOR_BACKENDS:
        try:
            result = DeepFace.represent(
                img_path=image_path,
                model_name=MODEL_NAME,
                enforce_detection=True,
                detector_backend=backend,
            )
            logger.info("Face detected with backend=%s", backend)
            return result[0]["embedding"]  # type: ignore[index]
        except Exception as exc:
            logger.warning("Face detection failed with backend=%s error=%s", backend, exc)

    logger.info("No reliable face detected with configured detectors")
    return None


def validate_uploaded_image(image_file) -> None:
    file_name = getattr(image_file, "name", "") or ""
    _, extension = os.path.splitext(file_name.lower())
    if extension and extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError("Unsupported image format.")

    file_size = getattr(image_file, "size", None)
    if file_size is not None and file_size > MAX_IMAGE_BYTES:
        raise ValueError("Image file is too large.")

    try:
        image_file.seek(0)
    except Exception:
        pass

    try:
        with Image.open(image_file) as image:
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Uploaded file is not a valid image.") from exc
    finally:
        try:
            image_file.seek(0)
        except Exception:
            pass

    try:
        with Image.open(image_file) as image:
            width, height = image.size
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Uploaded file could not be processed as an image.") from exc
    finally:
        try:
            image_file.seek(0)
        except Exception:
            pass

    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions are invalid.")
    if width * height > MAX_IMAGE_PIXELS:
        raise ValueError("Image resolution is too large.")


def extract_embedding_from_file(image_file):
    """
    Accept a Django uploaded file object, save it temporarily, extract an
    embedding, and delete the temporary file.
    """
    validate_uploaded_image(image_file)

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        for chunk in image_file.chunks():
            tmp.write(chunk)
        tmp_path = tmp.name

    logger.debug("Saved temporary face image path=%s bytes=%s", tmp_path, os.path.getsize(tmp_path))

    try:
        return extract_embedding_from_path(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError as exc:
            logger.warning("Failed to delete temporary face image path=%s error=%s", tmp_path, exc)


def cosine_similarity(vec_a: list, vec_b: list) -> float:
    a = np.array(vec_a, dtype=float)
    b = np.array(vec_b, dtype=float)
    if a.ndim != 1 or b.ndim != 1:
        raise ValueError("Embeddings must be one-dimensional vectors.")
    if a.shape != b.shape:
        raise ValueError("Embedding vectors must have the same shape.")
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def find_matching_employee(frame_embedding: list, employees) -> FaceMatchResult:
    """
    Compare a frame embedding against employees.

    A production match is accepted only when:
    - the best score is above FACE_MATCH_THRESHOLD
    - the best score is clearly separated from the second-best score
    """
    # Each employee can now have multiple stored samples. We score every sample
    # and keep the strongest score for that employee before comparing employees.
    scored_matches = []
    logger.info(
        "Matching employees threshold=%.2f margin=%.2f",
        FACE_MATCH_THRESHOLD,
        FACE_MATCH_MARGIN,
    )

    for emp in employees:
        employee_scores = []
        for stored in iter_employee_embeddings(emp):
            try:
                score = cosine_similarity(frame_embedding, stored)
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "Skipping employee id=%s due to invalid embedding vector error=%s",
                    getattr(emp, "pk", None),
                    exc,
                )
                continue
            employee_scores.append(score)

        if not employee_scores:
            logger.info("Skipping employee id=%s with no stored embedding", getattr(emp, "pk", None))
            continue

        score = max(employee_scores)
        logger.debug(
            "Face match candidate employee_id=%s score=%.4f samples=%s pass_threshold=%s",
            getattr(emp, "pk", None),
            score,
            len(employee_scores),
            score >= FACE_MATCH_THRESHOLD,
        )
        scored_matches.append((score, emp))

    if not scored_matches:
        return FaceMatchResult(
            employee=None,
            confidence=0.0,
            best_score=0.0,
            second_best_score=None,
            margin=None,
            is_match=False,
            is_ambiguous=False,
            decision="no_enrolled_faces",
        )

    scored_matches.sort(key=lambda item: item[0], reverse=True)
    best_score, best_match = scored_matches[0]
    second_best_score = scored_matches[1][0] if len(scored_matches) > 1 else None
    margin = best_score - second_best_score if second_best_score is not None else None
    confidence = round(best_score * 100, 2)

    logger.info(
        "Best face match employee_id=%s score=%.4f confidence=%.2f margin=%s",
        getattr(best_match, "pk", None),
        best_score,
        confidence,
        f"{margin:.4f}" if margin is not None else "n/a",
    )

    if best_score < FACE_MATCH_THRESHOLD:
        return FaceMatchResult(
            employee=None,
            confidence=confidence,
            best_score=best_score,
            second_best_score=second_best_score,
            margin=margin,
            is_match=False,
            is_ambiguous=False,
            decision="below_threshold",
        )

    if margin is not None and margin < FACE_MATCH_MARGIN:
        return FaceMatchResult(
            employee=best_match,
            confidence=confidence,
            best_score=best_score,
            second_best_score=second_best_score,
            margin=margin,
            is_match=False,
            is_ambiguous=True,
            decision="ambiguous_match",
        )

    return FaceMatchResult(
        employee=best_match,
        confidence=confidence,
        best_score=best_score,
        second_best_score=second_best_score,
        margin=margin,
        is_match=True,
        is_ambiguous=False,
        decision="accepted",
    )
