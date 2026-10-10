import os
import shutil
import tempfile
import asyncio
from fastapi import UploadFile
from pypdf import PdfReader
from pdf2image import convert_from_path
from chrome_lens_py import LensAPI
from google.cloud import vision
from langchain_text_splitters import RecursiveCharacterTextSplitter
import docx

class DocumentProcessor:
    def __init__(self, chunk_size=1000, chunk_overlap=150):
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", "।", " ", ""]
        )
        # Google Vision Client Initialize (Jodi credentials environment-e thake)
        self.vision_client = None
        if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            try:
                self.vision_client = vision.ImageAnnotatorClient()
            except Exception as e:
                print(f"[WARN] Google Vision setup failed: {e}")

    def process_docx(self, docx_path: str) -> list[str]:
        doc = docx.Document(docx_path)
        full_text = [para.text.strip() for para in doc.paragraphs if para.text.strip()]
        combined_text = "\n".join(full_text)
        if not combined_text.strip():
            return []
        return self.text_splitter.split_text(combined_text)

    # ── 1. PRIMARY OCR: GOOGLE CLOUD VISION API ─────────
    def _ocr_google_vision(self, image_path: str) -> str:
        if not self.vision_client:
            raise RuntimeError("Google Vision client not configured")
        
        with open(image_path, "rb") as image_file:
            content = image_file.read()

        image = vision.Image(content=content)
        # Bangla & English text detection
        image_context = vision.ImageContext(language_hints=["bn", "en"])
        response = self.vision_client.document_text_detection(image=image, image_context=image_context)

        if response.error.message:
            raise Exception(f"Google Vision API Error: {response.error.message}")

        return response.full_text_annotation.text.strip()

    # ── 2. FALLBACK OCR: CHROME LENS API ─────────────────
    async def _ocr_chrome_lens(self, api: LensAPI, image_path: str) -> str:
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

    # ── HYBRID OCR ROUTER ────────────────────────────────
    async def _run_hybrid_ocr(self, api: LensAPI, image_path: str) -> str:
        # Step A: Google Vision diye try kora
        try:
            print("[INFO] Attempting Google Cloud Vision OCR...")
            ocr_text = self._ocr_google_vision(image_path)
            if ocr_text:
                return ocr_text
        except Exception as e:
            print(f"[FALLBACK TRIGGERED] Google Vision error/quota reached: {e}")

        # Step B: Google Vision fail korle Chrome Lens API-te Fallback kora
        print("[INFO] Falling back to Chrome Lens API OCR...")
        return await self._ocr_chrome_lens(api, image_path)

    async def process_pdf_with_ocr(self, pdf_path: str) -> list[str]:
        reader = PdfReader(pdf_path)
        full_text = ""
        
        for page in reader.pages:
            extracted_text = page.extract_text()
            if extracted_text:
                full_text += extracted_text + "\n"

        # Digital PDF hole direct text splitter use korbe
        if len(full_text.strip()) > 50:
            print("[INFO] Digital text found in PDF.")
            return self.text_splitter.split_text(full_text)

        print("[INFO] Scanned PDF detected. Running Hybrid OCR Engine...")
        temp_dir = tempfile.mkdtemp()
        try:
            pages = convert_from_path(pdf_path, dpi=150)
            lens_api = LensAPI()
            ocr_results = []

            for index, page in enumerate(pages):
                image_path = os.path.join(temp_dir, f"page_{index+1:03d}.webp")
                page.save(image_path, "WEBP", quality=80)
                
                # Hybrid OCR (Vision -> Lens) call
                ocr_text = await self._run_hybrid_ocr(lens_api, image_path)
                if ocr_text:
                    ocr_results.append(ocr_text)
                
                await asyncio.sleep(0.3)

            combined_ocr_text = "\n\n".join(ocr_results)
            return self.text_splitter.split_text(combined_ocr_text)

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def process_file(self, file_path: str) -> list[str]:
        """ Extension onujayi file process korar master method """
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            return await self.process_pdf_with_ocr(file_path)
        elif ext in [".docx", ".doc"]:
            return self.process_docx(file_path)
        elif ext in [".png", ".jpg", ".jpeg", ".webp"]:
            lens_api = LensAPI()
            ocr_text = await self._run_hybrid_ocr(lens_api, file_path)
            return self.text_splitter.split_text(ocr_text)
        else:
            raise ValueError(f"Unsupported file format: {ext}")

    async def process_upload_file(self, upload_file: UploadFile) -> list[str]:
        """ FastAPI UploadFile object direct process korar helper """
        temp_dir = tempfile.mkdtemp()
        try:
            file_path = os.path.join(temp_dir, upload_file.filename)
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(upload_file.file, buffer)
            
            return await self.process_file(file_path)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

doc_processor = DocumentProcessor()