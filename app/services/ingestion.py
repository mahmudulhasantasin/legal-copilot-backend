import os
import shutil
import tempfile
import asyncio
from typing import List
from fastapi import UploadFile
from pypdf import PdfReader
import docx

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

    async def process_pdf_with_ocr(self, pdf_path: str) -> List[str]:
        reader = PdfReader(pdf_path)
        full_text = ""

        for page in reader.pages:
            extracted_text = page.extract_text()
            if extracted_text:
                full_text += extracted_text + "\n"

        if len(full_text.strip()) > 50:
            print("[INFO] Digital text found in PDF.")
            return self._split_text(full_text)

        if not convert_from_path:
            print("[WARN] pdf2image not installed. Returning extracted digital text.")
            return self._split_text(full_text)

        print("[INFO] Scanned PDF detected. Running Hybrid OCR Engine...")
        temp_dir = tempfile.mkdtemp()
        try:
            pages = convert_from_path(pdf_path, dpi=150)
            lens_api = None
            if LensAPI:
                try:
                    lens_api = LensAPI()
                except Exception as lens_init_err:
                    print(f"[WARN] Failed to initialize LensAPI: {lens_init_err}")

            ocr_results = []
            for index, page in enumerate(pages):
                image_path = os.path.join(temp_dir, f"page_{index+1:03d}.webp")
                page.save(image_path, "WEBP", quality=80)

                ocr_text = await self._run_hybrid_ocr(lens_api, image_path)
                if ocr_text:
                    ocr_results.append(ocr_text)

                await asyncio.sleep(0.3)

            combined_ocr_text = "\n\n".join(ocr_results)
            return self._split_text(combined_ocr_text)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def process_file(self, file_path: str) -> List[str]:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            return await self.process_pdf_with_ocr(file_path)
        elif ext in [".docx", ".doc"]:
            return self.process_docx(file_path)
        elif ext in [".png", ".jpg", ".jpeg", ".webp"]:
            lens_api = None
            if LensAPI:
                try:
                    lens_api = LensAPI()
                except Exception:
                    lens_api = None
            ocr_text = await self._run_hybrid_ocr(lens_api, file_path)
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
            doc = docx.Document(temp_file_path)
            return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

        if ext == ".pdf":
            reader = PdfReader(temp_file_path)
            extracted = []
            for page in reader.pages:
                txt = page.extract_text()
                if txt:
                    extracted.append(txt)
            raw = "\n".join(extracted).strip()

            if len(raw) > 50:
                return raw

            chunks = await doc_processor.process_pdf_with_ocr(temp_file_path)
            return "\n\n".join(chunks)

        raise ValueError(f"Unsupported file format: {ext}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)