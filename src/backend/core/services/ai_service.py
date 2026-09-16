"""Service for AI-powered features using OpenAI-compatible API."""
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
        logger.info(f"settings data: {settings}")
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

    def search_chunks(self, question: str) -> list[dict]:
        """Search relevant chunks in the Albert API vector store.

        The query must be a non-empty, non-whitespace string, otherwise the API
        answers 422 Unprocessable Entity.
        """
        question = (question or "").strip()
        if not question:
            # Ne jamais appeler l'API avec une requête vide : c'est un 422 garanti.
            return []
        logger.debug(f"{settings}")
        payload = {
            "query": question[:settings.AI_QUERY_MAX_CHARS],
            "method": settings.AI_SEARCH_METHOD,
            "limit": settings.AI_SEARCH_LIMIT,
        }
        if settings.AI_COLLECTION_IDS:
            logger.critical(f"settings.AI_COLLECTION_IDS: {settings.AI_COLLECTION_IDS}, {type(settings.AI_COLLECTION_IDS)}")
            collections_ids = settings.AI_COLLECTION_IDS
            if settings.AI_PRIVATE_COLLECTION_ID:
                collections_ids.append(settings.AI_PRIVATE_COLLECTION_ID)
            logger.critical(f'COLLECTIONS IDS USED: {collections_ids}')
            payload["collection_ids"] = collections_ids

        response = requests.post(
            url=f"{settings.AI_BASE_URL}/search",
            headers=self.headers,
            json=payload,
            timeout=60,
        )
        self.__check_response(response)
        # Réponse : {"object": "list", "data": [{"method", "score", "chunk": {...}}, ...]}
        return response.json()["data"]

    def call_ai_api(self, prompt):
        """Helper method to call the OpenAI API and process the response."""
        data = {
            "model": settings.AI_MODEL,
            "messages": [{"role": "user", "content": prompt}],
#            "stream": False,
#            "n": 1,
        }

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
                data={"collection_id": str(settings.AI_PRIVATE_COLLECTION_ID)},
                timeout=300,
            )
        response.raise_for_status()
        return response.json()["id"]

    def get_private_collections(self) -> list:
        """Return the dictionary of private collections."""
        return settings.AI_PRIVATE_COLLECTION_ID