"""Upload handling for LabSafe Agent attachments."""

import json
import mimetypes
import os
import time
import uuid

from werkzeug.utils import secure_filename

from .config import UPLOAD_DIR


IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
TEXT_EXTENSIONS = {"txt", "md", "json", "csv"}
OPTIONAL_DOC_EXTENSIONS = {"pdf", "docx"}


class UploadManager:
    def __init__(self, state_store, tools=None, config=None):
        self.state_store = state_store
        self.tools = tools
        self.config = config or {}
        self.upload_dir = self.config.get("dir") or UPLOAD_DIR
        self.max_bytes = int(float(self.config.get("max_file_mb", 12)) * 1024 * 1024)
        self.allowed_extensions = set(self.config.get("allowed_extensions") or [])
        self.text_preview_chars = int(self.config.get("text_preview_chars", 18000))
        self.thumbnail_max_px = int(self.config.get("thumbnail_max_px", 480))

    def save_upload(self, file_storage, trace_id=""):
        if not self.config.get("enabled", True):
            return {"success": False, "error": "uploads disabled"}
        if not file_storage or not getattr(file_storage, "filename", ""):
            return {"success": False, "error": "missing file"}

        original_name = file_storage.filename or "upload"
        safe_name = secure_filename(original_name) or "upload"
        ext = self._extension(safe_name)
        if not ext or ext not in self.allowed_extensions:
            return {"success": False, "error": f"unsupported file type: {ext or 'unknown'}"}

        data = file_storage.read(self.max_bytes + 1)
        if len(data) > self.max_bytes:
            return {"success": False, "error": f"file too large, limit {self.max_bytes // 1024 // 1024} MB"}

        os.makedirs(self.upload_dir, exist_ok=True)
        file_id = uuid.uuid4().hex[:16]
        stored_name = f"{file_id}.{ext}"
        file_path = os.path.join(self.upload_dir, stored_name)
        with open(file_path, "wb") as f:
            f.write(data)

        mime_type = file_storage.mimetype or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        kind = self._kind(ext)
        meta = {
            "file_id": file_id,
            "original_name": original_name,
            "stored_name": stored_name,
            "file_path": file_path,
            "thumbnail_path": "",
            "mime_type": mime_type,
            "kind": kind,
            "size_bytes": len(data),
            "parse_status": "saved",
            "extracted_text": "",
            "analysis": {},
            "created_at": time.time(),
        }

        if kind == "image":
            self._analyze_image(meta, trace_id)
        elif kind == "text":
            self._extract_text(meta, ext)
        else:
            meta["parse_status"] = "unsupported_parse"
            meta["analysis"] = {"message": "该格式已保存，但当前板端暂不解析 PDF/DOCX 内容。"}

        self.state_store.create_upload(meta)
        return {"success": True, "file": self._public_meta(meta)}

    def get_file_path(self, file_id, thumbnail=False):
        meta = self.state_store.get_upload(file_id)
        if not meta:
            return None
        path = meta.get("thumbnail_path") if thumbnail else meta.get("file_path")
        if not path or not os.path.exists(path):
            return None
        return path

    def get_public_meta(self, file_id):
        meta = self.state_store.get_upload(file_id)
        return self._public_meta(meta) if meta else None

    def context_for_ids(self, file_ids):
        metas = self.state_store.list_uploads(self._normalize_ids(file_ids))
        contexts = []
        for meta in metas:
            context = {
                "file_id": meta.get("file_id"),
                "name": meta.get("original_name"),
                "kind": meta.get("kind"),
                "mime_type": meta.get("mime_type"),
                "size_bytes": meta.get("size_bytes"),
                "parse_status": meta.get("parse_status"),
                "preview_url": meta.get("preview_url"),
                "thumbnail_url": meta.get("thumbnail_url"),
                "file_path": meta.get("file_path"),
            }
            text = meta.get("extracted_text") or ""
            if text:
                context["text"] = text[: self.text_preview_chars]
            analysis = meta.get("analysis") or {}
            if analysis:
                context["analysis"] = analysis
            contexts.append(context)
        return contexts

    def _analyze_image(self, meta, trace_id):
        analysis = {"type": "image"}
        dimensions = self._image_dimensions_and_thumbnail(meta)
        analysis.update(dimensions)
        if self.tools is not None:
            result = self.tools.analyze_uploaded_image(trace_id or meta["file_id"], meta["file_path"])
            analysis["local_detection"] = {
                "success": result.success,
                "error": result.error,
                "latency_ms": result.latency_ms,
                "data": result.data,
            }
        meta["analysis"] = analysis
        meta["parse_status"] = "parsed"

    def _image_dimensions_and_thumbnail(self, meta):
        try:
            import cv2
            import numpy as np

            data = np.fromfile(meta["file_path"], dtype=np.uint8)
            image = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if image is None:
                return {"image_error": "cannot decode image"}
            height, width = image.shape[:2]
            scale = min(1.0, float(self.thumbnail_max_px) / max(width, height))
            if scale < 1.0:
                thumb = cv2.resize(image, (int(width * scale), int(height * scale)))
            else:
                thumb = image
            thumb_path = os.path.join(self.upload_dir, f"{meta['file_id']}.thumb.jpg")
            if cv2.imwrite(thumb_path, thumb, [cv2.IMWRITE_JPEG_QUALITY, 82]):
                meta["thumbnail_path"] = thumb_path
            return {"width": width, "height": height}
        except Exception as e:
            return {"image_error": str(e)}

    def _extract_text(self, meta, ext):
        try:
            with open(meta["file_path"], "r", encoding="utf-8", errors="replace") as f:
                text = f.read(self.text_preview_chars + 1)
            if ext == "json":
                try:
                    text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
                except Exception:
                    pass
            meta["extracted_text"] = text[: self.text_preview_chars]
            meta["parse_status"] = "parsed"
            meta["analysis"] = {"chars": len(meta["extracted_text"])}
        except Exception as e:
            meta["parse_status"] = "parse_failed"
            meta["analysis"] = {"error": str(e)}

    def _public_meta(self, meta):
        if not meta:
            return None
        return {
            "file_id": meta.get("file_id"),
            "name": meta.get("original_name"),
            "type": meta.get("kind"),
            "mime": meta.get("mime_type"),
            "size": meta.get("size_bytes"),
            "parse_status": meta.get("parse_status"),
            "preview_url": f"/api/agent/uploads/{meta.get('file_id')}/content",
            "thumbnail_url": f"/api/agent/uploads/{meta.get('file_id')}/thumbnail" if meta.get("thumbnail_path") else "",
            "analysis": meta.get("analysis") or {},
        }

    def _kind(self, ext):
        if ext in IMAGE_EXTENSIONS:
            return "image"
        if ext in TEXT_EXTENSIONS:
            return "text"
        if ext in OPTIONAL_DOC_EXTENSIONS:
            return "document"
        return "file"

    @staticmethod
    def _extension(filename):
        _, ext = os.path.splitext(filename or "")
        return ext.lower().lstrip(".")

    @staticmethod
    def _normalize_ids(file_ids):
        if not file_ids:
            return []
        if isinstance(file_ids, str):
            file_ids = [file_ids]
        return [str(file_id).strip() for file_id in file_ids if str(file_id).strip()]
