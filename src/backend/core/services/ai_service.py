"""Service for AI-powered features using OpenAI-compatible API."""
import base64
import mimetypes
import logging
import requests
import json
import os
import ast

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from openai import OpenAI

from core.ai.utils import is_ai_enabled

logger = logging.getLogger(__name__)

# Extensions et types MIME acceptés par POST /v1/documents
ALLOWED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 Mo
OCR_TIMEOUT_SECONDS = 120
DOCUMENTS_PAGE_SIZE = 100  # maximum accepté par GET /v1/documents

class AIService:
    """Service class for AI-related operations."""

    def __init__(self):
        """Ensure that the AI configuration is set properly."""
        self.headers = {}
        if not is_ai_enabled():
            raise ImproperlyConfigured("AI configuration not set")
        self.client = OpenAI(
            base_url=settings.AI_BASE_URL,
            api_key=settings.AI_API_KEY,
            timeout=60,
            max_retries=1,
        )
        self.__set_headers()


    def __set_headers(self) -> dict:
        """Build API auth headers from settings/environment.

        Raises ImproperlyConfigured so the caller can fall back gracefully.
        """
        api_key = settings.AI_API_KEY
        if not api_key:
            raise ImproperlyConfigured(
                "AI_API_KEY is not configured (settings.AI_API_KEY or env var)."
            )
        self.headers = {"Authorization": f"Bearer {api_key}"}

    def __check_response(self, response: requests.Response) -> None:
        """Raise with the API's error detail included (a bare 422 hides the cause)."""
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            # Le corps de la réponse 422 indique le champ invalide, ex. :
            # {"detail":[{"type":"string_too_short","loc":["body","query"], ...}]}
            logger.error(
                "Albert API error %s on %s: %s",
                response.status_code,
                response.url,
                response.text[:1000],
            )
            raise requests.HTTPError(
                f"Albert API {response.status_code} on {response.url}: {response.text[:500]}"
            ) from exc

    def ocr_document(self, content: bytes, content_type: str) -> str:
        """Extract the text of a PDF, image or Word document with the OCR model.

        The file is sent inline as a base64 data URL; pages come back as
        Markdown and are joined in order.
        """
        data_url = f"data:{content_type};base64,{base64.b64encode(content).decode()}"
        chunk_type = "image_url" if content_type.startswith("image/") else "document_url"
        response = requests.post(
            url=f"{settings.AI_BASE_URL}/ocr",
            headers=self.headers,
            json={
                "model": settings.AI_OCR_MODEL,
                "document": {"type": chunk_type, chunk_type: data_url},
            },
            timeout=OCR_TIMEOUT_SECONDS,
        )
        self.__check_response(response)
        pages = response.json().get("pages", [])
        return "\n\n".join(page.get("markdown") or "" for page in pages).strip()

    def search_chunks(self, question: str) -> list[dict]:
        """Search relevant chunks in the Albert API vector store.

        The query must be a non-empty, non-whitespace string, otherwise the API
        answers 422 Unprocessable Entity.
        """
        question = (question or "").strip()
        if not question:
            # Ne jamais appeler l'API avec une requête vide : c'est un 422 garanti.
            return []
        payload = {
            "query": question[:settings.AI_QUERY_MAX_CHARS],
            "method": settings.AI_SEARCH_METHOD,
            "limit": settings.AI_SEARCH_LIMIT,
        }
        # Build a new list: appending to settings.AI_COLLECTION_IDS would add the
        # private collection again on every call.
        collection_ids = [int(collection_id) for collection_id in settings.AI_COLLECTION_IDS]
        if settings.AI_PRIVATE_COLLECTION_ID:
            collection_ids.append(int(settings.AI_PRIVATE_COLLECTION_ID))
        if collection_ids:
            payload["collection_ids"] = collection_ids

        response = requests.post(
            url=f"{settings.AI_BASE_URL}/search",
            headers=self.headers,
            json=payload,
            timeout=60,
        )
        self.__check_response(response)
        # Réponse : {"object": "list", "data": [{"method", "score", "chunk": {...}}, ...]}
        return response.json()["data"]

    def call_ai_api(self, prompt, system_prompt=None, seed=None):
        """Helper method to call the OpenAI API and process the response.

        ``system_prompt`` carries the fixed rules; ``prompt`` the content.
        ``seed`` makes the sampling reproducible for a given value; a new
        random seed on each call gives a different wording.
        """
        messages = [{"role": "user", "content": prompt}]
        if system_prompt:
            messages = [{"role": "system", "content": system_prompt}, *messages]
        data = {
            "model": settings.AI_MODEL,
            "messages": messages,
#            "stream": False,
#            "n": 1,
        }
        if seed is not None:
            data["seed"] = seed

        try:
            response = self.client.chat.completions.create(**data)
        except Exception:
            logger.exception("AI API call failed")
            raise

        if not response.choices:
            raise ValueError("AI response returned no choices")

        content = response.choices[0].message.content

        if not content:
            raise ValueError("AI response does not contain an answer")

        return content

    def upload_document(self, file_path: str) -> int:
        """Importe un fichier (PDF, TXT, HTML, MARKDOWN, max 20 Mo) dans la collection.

        L'API extrait le texte, le découpe en chunks, les vectorise puis les stocke.
        """
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Fichier introuvable : {file_path}")

        extension = os.path.splitext(file_path)[1].lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise ValueError(
                f"Format non accepté : '{extension or 'sans extension'}'. "
                f"Formats autorisés : {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            )

        file_size = os.path.getsize(file_path)
        if file_size > MAX_FILE_SIZE:
            raise ValueError(
                f"Fichier trop volumineux : {file_size / (1024 * 1024):.1f} Mo "
                f"(max {MAX_FILE_SIZE // (1024 * 1024)} Mo)"
            )

        # Type MIME déterminé depuis la liste blanche (prioritaire sur mimetypes,
        # qui peut renvoyer None ou une valeur inattendue selon la plateforme)
        mime_type = ALLOWED_EXTENSIONS[extension]

        with open(file_path, "rb") as f:
            response = requests.post(
                url=f"{settings.AI_BASE_URL}/documents",
                headers=self.headers,
                files={"file": (os.path.basename(file_path), f, mime_type)},
                data={"collection_id": str(self.__private_collection_id())},
                timeout=300,
            )
        self.__check_response(response)
        return response.json()["id"]

    def upload_text_document(self, name: str, content: str) -> int:
        """Import a Markdown text into the private collection under ``name``.

        The API chunks the text on Markdown separators, vectorizes and stores it.
        """
        if not name.strip():
            raise ValueError("Le nom du document est vide.")
        if not content.strip():
            raise ValueError(f"Le document '{name}' est vide.")
        payload = content.encode("utf-8")
        if len(payload) > MAX_FILE_SIZE:
            raise ValueError(
                f"Document trop volumineux : '{name}' "
                f"(max {MAX_FILE_SIZE // (1024 * 1024)} Mo)"
            )

        response = requests.post(
            url=f"{settings.AI_BASE_URL}/documents",
            headers=self.headers,
            files={"file": ("document.md", payload, ALLOWED_EXTENSIONS[".md"])},
            # ``name`` replaces the file name in the collection.
            data={"collection_id": str(self.__private_collection_id()), "name": name},
            timeout=300,
        )
        self.__check_response(response)
        return response.json()["id"]

    def find_document_ids_by_name(self, name: str) -> list[int]:
        """Return the ids of the private collection documents named ``name``."""
        document_ids = []
        offset = 0
        while True:
            response = requests.get(
                url=f"{settings.AI_BASE_URL}/documents",
                headers=self.headers,
                params={
                    "collection_id": self.__private_collection_id(),
                    "name": name,
                    "limit": DOCUMENTS_PAGE_SIZE,
                    "offset": offset,
                },
                timeout=60,
            )
            self.__check_response(response)
            documents = response.json()["data"]
            document_ids.extend(
                document["id"] for document in documents if document["name"] == name
            )
            if len(documents) < DOCUMENTS_PAGE_SIZE:
                return document_ids
            offset += DOCUMENTS_PAGE_SIZE

    def delete_document(self, document_id: int) -> None:
        """Delete a document of the collection; an already deleted one is ignored."""
        response = requests.delete(
            url=f"{settings.AI_BASE_URL}/documents/{document_id}",
            headers=self.headers,
            timeout=60,
        )
        if response.status_code == 404:
            return
        self.__check_response(response)

    @staticmethod
    def __private_collection_id() -> int:
        if not settings.AI_PRIVATE_COLLECTION_ID:
            raise ImproperlyConfigured("AI_PRIVATE_COLLECTION_ID is not configured.")
        return int(settings.AI_PRIVATE_COLLECTION_ID)

    def get_private_collections(self) -> list:
        """Return the dictionary of private collections."""
        return settings.AI_PRIVATE_COLLECTION_ID