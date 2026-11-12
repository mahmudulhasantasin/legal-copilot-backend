import os
import re
import shutil
import tempfile
import asyncio
from typing import List, Optional, Tuple
from fastapi import UploadFile
from pypdf import PdfReader
import docx

_BN_SCRIPT_RE = re.compile(r"[\u0980-\u09FF]")

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

    async def _run_hybrid_ocr(self, api, image_path: str) -> str:
        try:
            print("[INFO] Attempting Google Cloud Vision OCR...")
            ocr_text = self._ocr_google_vision(image_path)
            if ocr_text:
                return ocr_text
        except Exception as e:
            print(f"[FALLBACK TRIGGERED] Google Vision error/quota reached: {e}")

        if api:
            print("[INFO] Falling back to Chrome Lens API OCR...")
            return await self._ocr_chrome_lens(api, image_path)
        return ""

    @staticmethod
    def _should_run_pdf_ocr(reader: PdfReader, raw_text: str) -> Tuple[bool, str]:
        """Determines whether a PDF must go through image OCR instead of trusting pypdf text."""
        stripped = (raw_text or "").strip()
        if len(stripped) <= 50:
            return True, "sparse/scanned PDF text (<= 50 chars)"
        if _BN_SCRIPT_RE.search(stripped):
            return True, "Bengali script detected (using OCR instead of pypdf)"
        if any(len(getattr(page, "images", None) or []) > 0 for page in reader.pages):
            return True, "embedded image(s) detected in PDF"
        return False, "clean non-Bengali digital PDF"

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
        """Extracts PDF text, forcing OCR for Bengali PDFs, image-based PDFs, and scanned PDFs."""
        reader = PdfReader(pdf_path)
        extracted = []
        for page in reader.pages:
            txt = page.extract_text()
            if txt:
                extracted.append(txt)
        raw_text = "\n".join(extracted).strip()

        needs_ocr, reason = self._should_run_pdf_ocr(reader, raw_text)
        if not needs_ocr:
            print(f"[INFO] {reason}; returning pypdf digital text.")
            return raw_text

        print(f"[INFO] Running Hybrid OCR Engine ({reason})...")
        temp_dir = tempfile.mkdtemp()
        try:
            image_paths = self._render_pdf_to_images(pdf_path, temp_dir, dpi=150)
            lens_api = self._create_lens_api()

            ocr_results = []
            for image_path in image_paths:
                ocr_text = await self._run_hybrid_ocr(lens_api, image_path)
                if ocr_text:
                    ocr_results.append(ocr_text)
                await asyncio.sleep(0.3)

            combined_ocr_text = "\n\n".join(ocr_results).strip()
            if combined_ocr_text:
                return combined_ocr_text

            if raw_text:
                print("[WARN] Hybrid OCR returned empty text; falling back to pypdf extracted text.")
                return raw_text
            return ""
        except Exception as ocr_err:
            print(f"[WARN] PDF OCR failed ({ocr_err}).")
            if raw_text:
                print("[WARN] Falling back to pypdf extracted text.")
                return raw_text
            return ""
        finally:
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
            file_path = os.path.join(temp_dir, upload_file.filename)
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
    temp_file_path = os.path.join(temp_dir, filename)
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
