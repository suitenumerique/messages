"""Service for AI-powered features using OpenAI-compatible API."""

import logging
import requests
import json

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from openai import OpenAI

from core.ai.utils import is_ai_enabled

logger = logging.getLogger(__name__)


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
            payload["collection_ids"] = settings.AI_COLLECTION_IDS

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
