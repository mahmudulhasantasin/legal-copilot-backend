"""
Bounded upload reading + magic-byte validation (SEC-DOS-01 / SEC-INJ-01).

`read_upload_limited` streams an UploadFile in 1 MB chunks with a hard size
cap so a malicious upload can never fill server RAM before the size check.
`verify_magic_bytes` rejects files whose content doesn't match their claimed
extension (MIME-spoofing / extension-spoofing defense).
"""

from fastapi import HTTPException, UploadFile

CHUNK_SIZE = 1024 * 1024  # read 1 MB at a time

# Leading-byte signatures per extension. Extensions without a reliable
# signature (.txt) are accepted by default.
MAGIC_SIGNATURES = {
    ".pdf": [b"%PDF"],
    ".jpg": [b"\xFF\xD8\xFF"],
    ".jpeg": [b"\xFF\xD8\xFF"],
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".webp": [b"RIFF"],
    ".doc": [b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1", b"PK\x03\x04"],  # OLE2 or zip-wrapped
    ".docx": [b"PK\x03\x04", b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"],
}


def _too_large_detail(over_bytes: int, max_bytes: int) -> str:
    return (
        f"File too large. Max {max_bytes // (1024 * 1024)}MB allowed. "
        f"Your file: {round(over_bytes / (1024 * 1024), 1)}MB."
    )


async def read_upload_limited(file: UploadFile, max_bytes: int) -> bytes:
    """Read an UploadFile in chunks, raising HTTP 413 as soon as max_bytes is exceeded."""
    # Early exit on the declared size before reading anything.
    declared = getattr(file, "size", None)
    if declared and declared > max_bytes:
        raise HTTPException(413, _too_large_detail(declared, max_bytes))

    buf = bytearray()
    while True:
        chunk = await file.read(CHUNK_SIZE)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise HTTPException(413, _too_large_detail(len(buf), max_bytes))
    return bytes(buf)


def verify_magic_bytes(data: bytes, ext: str) -> bool:
    """True if the file's leading bytes match the claimed extension (or no signature exists)."""
    sigs = MAGIC_SIGNATURES.get(ext.lower())
    if not sigs:
        return True
    return any(data[: len(s)] == s for s in sigs)
