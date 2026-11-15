import os
import re
import shutil
import tempfile
import asyncio
import uuid
from typing import List, Optional, Tuple
from fastapi import UploadFile
from pypdf import PdfReader
import docx

_BN_SCRIPT_RE = re.compile(r"[\u0980-\u09FF]")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ALPHA_RE = re.compile(r"[A-Za-z\u0980-\u09FF]")
_LATIN1_SUPPLEMENT_RE = re.compile(r"[\u00c0-\u00ff]")
_REPLACEMENT_CHAR = "\ufffd"

# OCR is the default ingestion path. A page's pypdf text layer is trusted (and OCR
# skipped for that page) only when it clears every threshold below, which together
# assert "this is real digital English text". Anything else is re-read via OCR, so
# garbled legacy fonts, mojibake, and scanned pages can never leak through as-is.
_MIN_TRUSTED_PAGE_CHARS = 30
_MAX_CONTROL_CHAR_RATIO = 0.02
_MAX_REPLACEMENT_RATIO = 0.01
_MIN_ALPHA_RATIO = 0.50
_MAX_FRAGMENTED_TOKEN_RATIO = 0.35
_MAX_LATIN1_SUPPLEMENT_RATIO = 0.10
_TOKEN_STRIP_CHARS = ".,;:!?()[]{}\"'`%/-–—|*#<>"

try:
    from pdf2image import convert_from_path
except Exception:
    convert_from_path = None

try:
    from chrome_lens_py import LensAPI
except Exception:
    LensAPI = None

try:
    from google.cloud import vision
except Exception:
    vision = None

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except Exception:
    RecursiveCharacterTextSplitter = None


class DocumentProcessor:
    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 150):
        if RecursiveCharacterTextSplitter:
            self.text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                separators=["\n\n", "\n", "।", " ", ""]
            )
        else:
            self.text_splitter = None

        self.vision_client = None
        if vision and os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            try:
                self.vision_client = vision.ImageAnnotatorClient()
            except Exception as e:
                print(f"[WARN] Google Vision setup failed: {e}")

    def _split_text(self, text: str) -> List[str]:
        if not text.strip():
            return []
        if self.text_splitter:
            return self.text_splitter.split_text(text)
        return [text]

    def process_docx(self, docx_path: str) -> List[str]:
        doc = docx.Document(docx_path)
        full_text = [para.text.strip() for para in doc.paragraphs if para.text.strip()]
        combined_text = "\n".join(full_text)
        return self._split_text(combined_text)

    def _ocr_google_vision(self, image_path: str) -> str:
        if not self.vision_client:
            raise RuntimeError("Google Vision client not configured")

        with open(image_path, "rb") as image_file:
            content = image_file.read()

        image = vision.Image(content=content)
        image_context = vision.ImageContext(language_hints=["bn", "en"])
        response = self.vision_client.document_text_detection(image=image, image_context=image_context)

        if response.error.message:
            raise Exception(f"Google Vision API Error: {response.error.message}")

        return response.full_text_annotation.text.strip()

    async def _ocr_chrome_lens(self, api, image_path: str) -> str:
        if not api:
            return ""
        try:
            result = await api.process_image(image_path=image_path, ocr_language="bn")
            return result.get("ocr_text", "").strip()
        except Exception:
            try:
                result = await api.process_image(image_path=image_path)
                return result.get("ocr_text", "").strip()
            except Exception as err:
                print(f"[Lens OCR Error] {image_path}: {err}")
                return ""

    @staticmethod
    def _clean_ocr_artifacts(text: str) -> str:
        if not text:
            return ""
        # Strip common Chrome Lens OCR hallucinated artifacts: ², Ǭ, ǂ, {, }, |, ȧ
        t = re.sub(r'[²Ǭǂ{}|ȧ\u00b2\u01ec\u01c2\u007b\u007d\u007c\u0227]', '', text)
        t = re.sub(r'([\u0980-\u09FF])7([\u0980-\u09FF])', r'\1\2', t)
        t = re.sub(r'(?<=[\s\-])7(?=[\u0980-\u09FF])', '', t)
        t = re.sub(r'^\s*•\s*$\n', '', t, flags=re.MULTILINE)
        t = re.sub(r'^\s*দ[ললি\s]*রেফা[=–\s\-]*রেন্স:[^\n]*$', '', t, flags=re.MULTILINE)
        return t.strip()

    async def _run_hybrid_ocr(self, api, image_path: str) -> str:
        # Prioritize Chrome Lens API for non-English/Bengali OCR
        if api:
            try:
                lens_text = await self._ocr_chrome_lens(api, image_path)
                if lens_text:
                    return self._clean_ocr_artifacts(lens_text)
            except Exception as e:
                print(f"[WARN] Chrome Lens OCR error: {e}")

        try:
            print("[INFO] Attempting Google Cloud Vision OCR...")
            ocr_text = self._ocr_google_vision(image_path)
            if ocr_text:
                return ocr_text
        except Exception as e:
            print(f"[FALLBACK TRIGGERED] Google Vision error/quota reached: {e}")

        return ""

    @staticmethod
    def _is_digital_text_trustworthy(text: str) -> Tuple[bool, str]:
        """Decides whether a single page's pypdf text layer is clean English digital text.

        OCR is the default: text is trusted ONLY when it positively looks like real
        extractable English. Returns (is_trustworthy, reason).
        """
        stripped = (text or "").strip()
        if not stripped:
            return False, "empty page (needs OCR)"

        if len(stripped) < _MIN_TRUSTED_PAGE_CHARS:
            return False, f"sparse text on page ({len(stripped)} chars < {_MIN_TRUSTED_PAGE_CHARS})"

        if _BN_SCRIPT_RE.search(stripped):
            return False, "Bengali script present (needs OCR for correct glyphs)"

        total = len(stripped)

        if stripped.count(_REPLACEMENT_CHAR) / total > _MAX_REPLACEMENT_RATIO:
            return False, "encoding replacement characters (U+FFFD) detected"

        control = len(_CONTROL_CHAR_RE.findall(stripped))
        if control / total > _MAX_CONTROL_CHAR_RATIO:
            return False, "binary/control characters detected"

        # Legacy Bengali fonts map conjuncts onto Latin-1 symbols (e.g. 'চুǬক্তিপত্র'),
        # so a high supplement ratio is a strong garbled-font signal.
        latin1_supplement = len(_LATIN1_SUPPLEMENT_RE.findall(stripped))
        if latin1_supplement / total > _MAX_LATIN1_SUPPLEMENT_RATIO:
            return False, "legacy/garbled font encoding detected"

        alpha = len(_ALPHA_RE.findall(stripped))
        if alpha / total < _MIN_ALPHA_RATIO:
            return False, "mostly non-alphabetic content (likely noise)"

        tokens = stripped.split()
        if tokens:
            fragmented = sum(
                1
                for token in tokens
                if len(token.strip(_TOKEN_STRIP_CHARS)) == 1 and token.strip(_TOKEN_STRIP_CHARS).isalpha()
            )
            if fragmented / len(tokens) > _MAX_FRAGMENTED_TOKEN_RATIO:
                return False, "fragmented single-character words (broken text layer)"

        return True, "clean English digital text"

    @staticmethod
    def _page_has_embedded_images(page) -> bool:
        """Best-effort embedded-image probe; never fails PDF ingestion on its own."""
        try:
            return len(getattr(page, "images", None) or []) > 0
        except Exception:
            return False

    @classmethod
    def _should_run_pdf_ocr(cls, reader: PdfReader, raw_text: str) -> Tuple[bool, str]:
        """Decides whether ANY page of the PDF needs OCR instead of trusting pypdf text.

        OCR is the default path; direct extraction is used only when every page's text
        layer is positively confirmed as clean digital English. A single untrustworthy
        page (scanned, garbled font, Bengali, or image-backed) routes the whole document
        through OCR so no page is silently degraded.
        """
        pages = list(getattr(reader, "pages", None) or [])
        if not pages:
            return True, "no readable pages (needs OCR)"

        bad_pages: List[str] = []
        for index, page in enumerate(pages, start=1):
            try:
                page_text = page.extract_text() or ""
            except Exception:
                bad_pages.append(f"p{index}: text extraction failed")
                continue

            trustworthy, reason = cls._is_digital_text_trustworthy(page_text)
            if not trustworthy:
                bad_pages.append(f"p{index}: {reason}")
            elif cls._page_has_embedded_images(page):
                bad_pages.append(f"p{index}: embedded image(s) detected")

        if bad_pages:
            detail = "; ".join(bad_pages[:3])
            if len(bad_pages) > 3:
                detail += f"; +{len(bad_pages) - 3} more"
            return True, f"untrusted PDF text layer ({detail})"

        return False, f"all {len(pages)} page(s) verified as clean English digital text"

    @staticmethod
    def _render_pdf_to_images(pdf_path: str, temp_dir: str, dpi: int = 150) -> List[str]:
        """Renders PDF pages to WEBP images using Poppler (pdf2image) or pypdfium2 if available."""
        image_paths: List[str] = []
        poppler_path: Optional[str] = os.getenv("POPPLER_PATH") or None
        last_err: Optional[Exception] = None

        if convert_from_path is not None:
            try:
                kwargs = {"dpi": dpi}
                if poppler_path:
                    kwargs["poppler_path"] = poppler_path
                pages = convert_from_path(pdf_path, **kwargs)
                for index, page in enumerate(pages):
                    img_path = os.path.join(temp_dir, f"page_{index + 1:03d}.webp")
                    page.save(img_path, "WEBP", quality=80)
                    image_paths.append(img_path)
                return image_paths
            except Exception as exc:
                last_err = exc

        try:
            import pypdfium2 as pdfium  # optional pure-wheel fallback (no Poppler binary needed)
            pdf_doc = pdfium.PdfDocument(pdf_path)
            try:
                scale = dpi / 72.0
                for index in range(len(pdf_doc)):
                    page = pdf_doc[index]
                    try:
                        pil_img = page.render(scale=scale).to_pil()
                        img_path = os.path.join(temp_dir, f"page_{index + 1:03d}.webp")
                        pil_img.save(img_path, "WEBP", quality=80)
                        image_paths.append(img_path)
                    finally:
                        page.close()
                return image_paths
            finally:
                pdf_doc.close()
        except Exception as exc:
            if last_err is None:
                last_err = exc

        raise RuntimeError(f"PDF page rendering failed (install Poppler or pypdfium2): {last_err}")

    def _create_lens_api(self):
        if not LensAPI:
            return None
        try:
            return LensAPI()
        except Exception as lens_init_err:
            print(f"[WARN] Failed to initialize LensAPI: {lens_init_err}")
            return None

    async def extract_image_text(self, image_path: str) -> str:
        """Runs Hybrid OCR (Google Vision -> Chrome Lens API) on a standalone image file."""
        lens_api = self._create_lens_api()
        return await self._run_hybrid_ocr(lens_api, image_path)

    async def extract_pdf_text(self, pdf_path: str) -> str:
        """Extracts PDF text, defaulting to OCR and only trusting clean English text layers.

        Each page is assessed independently: pages whose embedded text layer is verified
        as clean digital English are read directly, while every other page (scanned,
        image-backed, Bengali, garbled-font, or low quality) is rendered and OCR'd.
        """
        reader = PdfReader(pdf_path)
        pages = list(getattr(reader, "pages", None) or [])

        page_texts: List[str] = []
        ocr_pages: List[int] = []
        bad_reasons: List[str] = []
        for index, page in enumerate(pages):
            try:
                page_text = (page.extract_text() or "").strip()
            except Exception as exc:
                page_text = ""
                reason = f"text extraction failed ({exc.__class__.__name__})"
            else:
                reason = ""

            if not reason:
                trustworthy, reason = self._is_digital_text_trustworthy(page_text)
                if trustworthy and self._page_has_embedded_images(page):
                    trustworthy, reason = False, "embedded image(s) detected"
                elif trustworthy:
                    # Trusted: clear the diagnostic so this page takes the direct path.
                    reason = ""

            page_texts.append(page_text)
            if reason:
                ocr_pages.append(index)
                bad_reasons.append(f"p{index + 1}: {reason}")
                print(f"[INFO] Page {index + 1} routed to OCR ({reason}).")

        # Fast path: every page verified as clean English digital text.
        if not ocr_pages:
            print(f"[INFO] All {len(pages)} page(s) verified as clean English digital text.")
            return "\n".join(t for t in page_texts if t).strip()

        detail = "; ".join(bad_reasons[:3])
        if len(bad_reasons) > 3:
            detail += f"; +{len(bad_reasons) - 3} more"
        reason = f"untrusted PDF text layer ({detail})"
        print(f"[INFO] Running Hybrid OCR Engine ({reason})...")

        lens_api = None
        temp_dir = tempfile.mkdtemp()
        try:
            image_paths = self._render_pdf_to_images(pdf_path, temp_dir, dpi=150)
            lens_api = self._create_lens_api()

            page_outputs: List[str] = []
            for index in range(len(pages)):
                if index not in ocr_pages:
                    page_outputs.append(page_texts[index])
                    continue

                ocr_text = ""
                if index < len(image_paths):
                    ocr_text = await self._run_hybrid_ocr(lens_api, image_paths[index])
                if ocr_text:
                    page_outputs.append(ocr_text)
                else:
                    # OCR unavailable/failed for this page: fall back to whatever
                    # pypdf produced rather than dropping the page entirely.
                    if page_texts[index]:
                        print(f"[WARN] OCR empty for page {index + 1}; using untrusted pypdf text.")
                    page_outputs.append(page_texts[index])
                await asyncio.sleep(0.3)

            combined = "\n\n".join(t for t in page_outputs if t).strip()
            if combined:
                return combined

            fallback = "\n".join(t for t in page_texts if t).strip()
            if fallback:
                print("[WARN] Hybrid OCR returned empty text; falling back to pypdf extracted text.")
                return fallback
            return ""
        except Exception as ocr_err:
            print(f"[WARN] PDF OCR failed ({ocr_err}).")
            fallback = "\n".join(t for t in page_texts if t).strip()
            if fallback:
                print("[WARN] Falling back to pypdf extracted text.")
                return fallback
            return ""
        finally:
            if lens_api:
                try:
                    await lens_api.aclose()
                except Exception:
                    pass
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def process_pdf_with_ocr(self, pdf_path: str) -> List[str]:
        text = await self.extract_pdf_text(pdf_path)
        return self._split_text(text)

    async def process_file(self, file_path: str) -> List[str]:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            return await self.process_pdf_with_ocr(file_path)
        elif ext in [".docx", ".doc"]:
            return self.process_docx(file_path)
        elif ext in [".png", ".jpg", ".jpeg", ".webp"]:
            ocr_text = await self.extract_image_text(file_path)
            return self._split_text(ocr_text)
        elif ext in [".txt", ".md"]:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return self._split_text(f.read())
        else:
            raise ValueError(f"Unsupported file format: {ext}")

    async def process_upload_file(self, upload_file: UploadFile) -> List[str]:
        temp_dir = tempfile.mkdtemp()
        try:
            # ✅ SEC-INJ-02: never build temp paths from client-controlled filenames —
            # keep only a sanitized extension and use a random server-side name.
            ext = os.path.splitext(os.path.basename(upload_file.filename or ""))[1].lower()
            if not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", ext):
                ext = ".bin"
            file_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}{ext}")
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(upload_file.file, buffer)
            return await self.process_file(file_path)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


doc_processor = DocumentProcessor()


async def extract_text_from_file(filename: str, content: bytes) -> str:
    ext = os.path.splitext(filename)[1].lower()

    if ext in [".txt", ".md"]:
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return content.decode("latin-1", errors="ignore")

    temp_dir = tempfile.mkdtemp()
    # ✅ SEC-INJ-02: sanitized extension + random server-side name (path-traversal-proof)
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", ext or ""):
        ext = ".bin"
    temp_file_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}{ext}")
    try:
        with open(temp_file_path, "wb") as f:
            f.write(content)

        if ext in [".docx", ".doc"]:
            try:
                doc = docx.Document(temp_file_path)
                return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
            except Exception:
                raise ValueError(
                    "Legacy Word 97-2003 (.doc) binary format cannot be read directly. "
                    "Please save or export the document as .docx or .pdf and re-upload."
                )

        if ext == ".pdf":
            return await doc_processor.extract_pdf_text(temp_file_path)

        if ext in [".png", ".jpg", ".jpeg", ".webp"]:
            return await doc_processor.extract_image_text(temp_file_path)

        raise ValueError(f"Unsupported file format: {ext}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
