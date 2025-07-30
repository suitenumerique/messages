import os
import json
import requests
import logging
import wget
import uuid
import hashlib
import time
import tempfile

from .config import AlbertConfig
from .api_client import AlbertAPIClient

logger = logging.getLogger(__name__)

class RAGSystem:
    """System for Retrieval-Augmented Generation with Albert API."""

    def __init__(self):
        """Initialize the RAG system."""
        self.config = AlbertConfig()
        
        # Validate that base_url is properly configured
        if not self.config.base_url:
            raise ValueError("AI_BASE_URL environment variable is not set or is empty. Please configure it with the Albert API base URL.")
        
        if not self.config.base_url.startswith(('http://', 'https://')):
            raise ValueError(f"Invalid base URL '{self.config.base_url}'. URL must start with http:// or https://")
        
        logger.info(f"RAG System initialized with base URL: {self.config.base_url}")
        
        self.api_client = AlbertAPIClient(self.config)
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {self.config.api_key}',
        })
        self.collection_name = None  # Will be set based on user_id
        self.collection_id = None
        self.embeddings_model = None
        self.indexed_email_ids = set()  # Track which emails are already indexed
        self.last_indexed_hash = None  # Hash of last indexed email set
        self.last_index_time = None  # Timestamp of last indexing
        self._state_file_dir = os.path.join(tempfile.gettempdir(), 'rag_state')
        os.makedirs(self._state_file_dir, exist_ok=True)

    def _get_state_file_path(self):
        """Get the path to the state file for the current collection."""
        if not self.collection_name:
            return None
        safe_name = self.collection_name.replace('/', '_').replace('\\', '_')
        return os.path.join(self._state_file_dir, f"{safe_name}_state.json")

    def _save_state(self):
        """Save the current indexing state to disk."""
        state_file = self._get_state_file_path()
        if not state_file:
            return
            
        try:
            state = {
                'collection_id': self.collection_id,
                'last_indexed_hash': self.last_indexed_hash,
                'last_index_time': self.last_index_time,
                'indexed_email_ids': list(self.indexed_email_ids)
            }
            with open(state_file, 'w') as f:
                json.dump(state, f)
            logger.debug(f"Saved state to {state_file}")
        except Exception as e:
            logger.warning(f"Failed to save state: {e}")

    def _load_state(self):
        """Load the indexing state from disk."""
        state_file = self._get_state_file_path()
        if not state_file or not os.path.exists(state_file):
            return
            
        try:
            with open(state_file, 'r') as f:
                state = json.load(f)
            
            # Only restore state if collection_id matches
            if state.get('collection_id') == self.collection_id:
                self.last_indexed_hash = state.get('last_indexed_hash')
                self.last_index_time = state.get('last_index_time')
                self.indexed_email_ids = set(state.get('indexed_email_ids', []))
                logger.info(f"Restored state: hash={self.last_indexed_hash is not None}, "
                           f"time={self.last_index_time}, emails={len(self.indexed_email_ids)}")
            else:
                logger.debug(f"Collection ID mismatch, not loading state")
        except Exception as e:
            logger.warning(f"Failed to load state: {e}")

    def _get_models(self):
        """Get available models from the API using direct HTTP requests."""
        if self.embeddings_model:
            return
        
        # Use direct HTTP requests instead of OpenAI client to avoid compatibility issues
        # Following the Albert API documentation pattern
        try:
            response = self.session.get(f"{self.config.base_url}/models")
            response.raise_for_status()
            models_data = response.json()
            
            # Look for embedding model
            for model in models_data.get('data', []):
                if model.get('type') == "text-embeddings-inference":
                    self.embeddings_model = model.get('id')
                    break
                    
            if not self.embeddings_model:
                # Fallback to a default embedding model if none found
                self.embeddings_model = "embeddings-small"
                
        except Exception as e:
            logger.error(f"Failed to get models from Albert API: {e}")
            # Use default embedding model as fallback
            self.embeddings_model = "embeddings-small"

    def collection_exists(self, user_id: str) -> bool:
        """
        Check if a collection exists for a user without setting up the full state.
        This is more efficient for quick checks.
        
        Args:
            user_id: The user ID to check
            
        Returns:
            True if collection exists, False otherwise
        """
        try:
            collection_name = f"email-collection-user-{user_id}"
            logger.debug(f"Checking if collection exists: {collection_name}")
            
            response = self.session.get(f"{self.config.base_url}/collections")
            response.raise_for_status()
            collections = response.json().get('data', [])
            
            for collection in collections:
                if collection.get('name') == collection_name:
                    logger.debug(f"Collection exists for user {user_id}: {collection.get('id')}")
                    return True
            
            logger.debug(f"No collection found for user {user_id}")
            return False
            
        except Exception as e:
            logger.error(f"Error checking collection existence for user {user_id}: {e}")
            return False

    def set_user_collection(self, user_id: str):
        """Set the collection name based on user ID for persistence."""
        new_collection_name = f"email-collection-user-{user_id}"
        
        # Only reset state if we're switching to a different user
        if self.collection_name != new_collection_name:
            self.collection_name = new_collection_name
            # Reset collection_id and indexed_email_ids as we're switching users
            self.collection_id = None
            self.indexed_email_ids = set()
            self.last_indexed_hash = None
            self.last_index_time = None
            logger.debug(f"Switched to user collection: {self.collection_name}")
            
            # Check if the collection exists for this user
            if self._get_existing_collection():
                logger.debug(f"Found existing collection for {self.collection_name}: {self.collection_id}")
            else:
                logger.debug(f"No existing collection found for {self.collection_name}")
        else:
            # Same user, keep existing state
            self.collection_name = new_collection_name
            logger.debug(f"Keeping existing state for user collection: {self.collection_name}")

    def collection_exists(self, user_id: str = None) -> bool:
        """
        Check if a collection exists for a user without fully initializing the RAG system.
        
        Args:
            user_id: Optional user ID. If not provided, uses current collection_name.
            
        Returns:
            True if collection exists, False otherwise
        """
        if user_id:
            collection_name = f"email-collection-user-{user_id}"
        else:
            collection_name = self.collection_name
            
        if not collection_name:
            return False
            
        try:
            response = self.session.get(f"{self.config.base_url}/collections")
            response.raise_for_status()
            collections = response.json().get('data', [])
            
            for collection in collections:
                if collection.get('name') == collection_name:
                    return True
            return False
            
        except Exception as e:
            logger.warning(f"Error checking if collection exists: {e}")
            return False

    def _compute_email_hash(self, emails: list[dict]) -> str:
        """Compute a hash of the email set to detect changes."""
        email_ids = sorted([email.get('id', '') for email in emails])
        content = '|'.join(email_ids)
        return hashlib.md5(content.encode()).hexdigest()

    def _needs_reindexing(self, emails: list[dict]) -> bool:
        """Check if reindexing is needed based on email changes and time."""
        current_hash = self._compute_email_hash(emails)
        current_time = time.time()
        
        # Always reindex if no previous hash (first time or collection recreated)
        if not self.last_indexed_hash:
            logger.info("No previous indexing hash found, indexing needed")
            return True
            
        # Reindex if email set has changed significantly
        if current_hash != self.last_indexed_hash:
            logger.info("Email set has changed, reindexing needed")
            return True
            
        # Reindex if it's been more than 2 hours since last indexing (to handle any API issues)
        if self.last_index_time and (current_time - self.last_index_time) > 7200:
            logger.info("More than 2 hours since last indexing, reindexing for freshness")
            return True
            
        logger.info("Email set unchanged and recently indexed, skipping reindexing")
        return False

    def _get_existing_collection(self):
        """Check if a collection with the current name already exists."""
        try:
            logger.debug(f"Fetching collections from {self.config.base_url}/collections")
            response = self.session.get(f"{self.config.base_url}/collections")
            response.raise_for_status()
            collections = response.json().get('data', [])
            
            logger.debug(f"Found {len(collections)} total collections")
            collection_names = [c.get('name', 'unnamed') for c in collections]
            logger.debug(f"Collection names: {collection_names}")
            
            for collection in collections:
                if collection.get('name') == self.collection_name:
                    self.collection_id = collection.get('id')
                    logger.info(f"Found existing collection '{self.collection_name}' with ID: {self.collection_id}")
                    self._load_indexed_emails()
                    return True
            
            logger.info(f"No existing collection found with name: {self.collection_name}")
            return False
        except Exception as e:
            logger.error(f"Error checking for existing collection: {e}")
            return False

    def _load_indexed_emails(self):
        """Initialize tracking for the existing collection."""
        if not self.collection_id:
            return
            
        logger.info(f"Collection '{self.collection_name}' exists with ID: {self.collection_id}")
        
        # Try to load the persistent state
        self._load_state()
        
        # Log what we restored
        if self.last_indexed_hash:
            logger.info(f"Restored indexing state from previous session")
        else:
            logger.info(f"No previous indexing state found, will reindex on first run")

    def create_collection(self):
        """Create a new collection for emails or use existing one."""
        if not self.collection_name:
            raise Exception("Collection name not set. Call set_user_collection() first.")
            
        self._get_models()
        if not self.embeddings_model:
            raise Exception("No embedding model found.")

        # Check if collection already exists
        logger.info(f"Checking for existing collection: {self.collection_name}")
        if self._get_existing_collection():
            logger.info(f"Reusing existing collection: {self.collection_name} (ID: {self.collection_id})")
            return

        # Create new collection
        logger.info(f"Creating new collection: {self.collection_name}")
        response = self.session.post(
            f"{self.config.base_url}/collections",
            json={"name": self.collection_name, "model": self.embeddings_model}
        )
        response.raise_for_status()
        self.collection_id = response.json()["id"]
        logger.info(f"Collection '{self.collection_name}' created with ID: {self.collection_id}")

    def clear_collection(self):
        """Clear all documents from the current collection."""
        if not self.collection_id:
            return
            
        try:
            # Clear the collection by deleting it and recreating it
            # This is often easier than trying to delete individual documents
            response = self.session.delete(f"{self.config.base_url}/collections/{self.collection_id}")
            response.raise_for_status()
            logger.info(f"Deleted collection {self.collection_id}")
            
            # Reset state
            self.collection_id = None
            self.indexed_email_ids = set()
            self.last_indexed_hash = None
            self.last_index_time = None
            
            # Recreate the collection
            self.create_collection()
            
        except Exception as e:
            logger.error(f"Error clearing collection: {e}")
            # Reset state anyway
            self.collection_id = None
            self.indexed_email_ids = set()
            self.last_indexed_hash = None
            self.last_index_time = None

    def add_single_email(self, email: dict):
        """
        Add a single email to the collection without triggering full reindexing.
        This is efficient for real-time updates when new messages arrive.
        
        Args:
            email: Dictionary with 'id', 'body', and other email fields
        """
        if not self.collection_id:
            logger.warning("Cannot add single email: no collection exists")
            return False
            
        email_id = email.get('id')
        if not email_id:
            logger.error("Cannot add email without ID")
            return False
            
        # Check if already indexed
        if email_id in self.indexed_email_ids:
            logger.debug(f"Email {email_id} already indexed, skipping")
            return True
            
        try:
            logger.info(f"Adding single email {email_id} to collection {self.collection_id}")
            
            # Prepare document for indexing
            documents = [{
                "id": email_id,
                "content": email.get('body', ''),
                "metadata": {
                    "subject": email.get('subject', ''),
                    "sender": email.get('sender', ''),
                    "created_at": email.get('created_at', ''),
                    "email_id": email_id
                }
            }]
            
            # Add to collection
            response = self.session.post(
                f"{self.config.base_url}/collections/{self.collection_id}/documents",
                json={"documents": documents}
            )
            response.raise_for_status()
            
            # Update tracking
            self.indexed_email_ids.add(email_id)
            self.last_index_time = time.time()
            self._save_state()
            
            logger.info(f"✅ Successfully added email {email_id} to collection")
            return True
            
        except Exception as e:
            logger.error(f"Failed to add single email {email_id}: {e}")
            return False

    def index_emails(self, emails: list[dict]):
        """
        Index a list of emails, with smart reindexing detection.

        Args:
            emails: A list of dictionaries, where each dictionary represents an email
                    with at least a 'body' key and 'id' key.
        """
        if not self.collection_id:
            self.create_collection()

        # Check if reindexing is needed
        if not self._needs_reindexing(emails):
            logger.info("Skipping indexing: emails already indexed and up to date")
            return

        indexing_start_time = time.time()
        logger.info(f"Indexing {len(emails)} emails (reindexing needed)")
        
        # Log attachment information in the emails being indexed
        emails_with_attachments = 0
        total_attachments = 0
        sample_attachments = []
        
        for email in emails[:10]:  # Check first 10 emails for attachment info
            email_body = email.get('body', '')
            if 'Files attached:' in email_body:
                emails_with_attachments += 1
                # Extract attachment names from the body
                attachment_section = email_body.split('Files attached:')[-1].strip()
                if attachment_section:
                    attachments = [att.strip() for att in attachment_section.split(',')]
                    total_attachments += len(attachments)
                    sample_attachments.extend(attachments[:3])  # Sample first 3
        
        if emails_with_attachments > 0:
            logger.info(f"📎 RAG INDEXING: {emails_with_attachments} emails with attachments in first 10 emails")
            logger.info(f"📎 RAG SAMPLE ATTACHMENTS: {sample_attachments[:5]}")

        successful_indexes = 0
        failed_indexes = 0

        for i, email in enumerate(emails):
            try:
                # Limit email body size to avoid API issues, but preserve attachment info
                email_body = email.get('body', '')
                
                # Log if this email has attachment information
                has_attachments_info = 'Files attached:' in email_body
                attachment_section = ""
                
                if has_attachments_info:
                    logger.debug(f"Email {i+1} ({email.get('id', 'unknown')}) contains attachment information")
                    # Extract attachment section to preserve it
                    parts = email_body.split('Files attached:')
                    if len(parts) > 1:
                        attachment_section = '\n\nFiles attached:' + parts[-1]
                
                if len(email_body) > self.config.max_email_content_length:
                    if has_attachments_info and attachment_section:
                        # Calculate space needed for attachment section
                        attachment_length = len(attachment_section)
                        available_length = self.config.max_email_content_length - attachment_length - 20  # 20 chars for "... [truncated]"
                        
                        if available_length > 500:  # Ensure we have reasonable content left
                            # Truncate main content but preserve attachments
                            main_content = email_body.replace(attachment_section, '')
                            truncated_main = main_content[:available_length] + "... [truncated]"
                            email_body = truncated_main + attachment_section
                            logger.info(f"Email {i+1} truncated but attachment information preserved")
                        else:
                            # Not enough space, just truncate normally and warn
                            email_body = email_body[:self.config.max_email_content_length] + "... [truncated]"
                            logger.warning(f"Email {i+1} truncated and may have lost attachment information")
                    else:
                        # No attachments, just truncate normally
                        email_body = email_body[:self.config.max_email_content_length] + "... [truncated]"
                
                # Clean the email body to remove potential problematic characters
                email_body = email_body.encode('utf-8', errors='ignore').decode('utf-8')
                
                # Create a temporary file with the email body
                file_path = f"/tmp/email_{i}_{email.get('id', 'unknown')}.txt"
                with open(file_path, "w", encoding='utf-8') as f:
                    f.write(email_body)

                # Prepare metadata with email ID for better matching
                metadata = email.get('metadata', {})
                metadata['email_id'] = email.get('id', 'unknown')
                metadata['document_name'] = os.path.basename(file_path)

                with open(file_path, "rb") as f:
                    files = {'file': (os.path.basename(file_path), f, 'text/plain')}
                    # Include metadata in the request
                    request_data = {
                        "collection": self.collection_id,
                        "metadata": metadata
                    }
                    data = {'request': json.dumps(request_data)}
                    
                    response = self.session.post(
                        f"{self.config.base_url}/files",
                        data=data,
                        files=files,
                        timeout=30  # Add timeout
                    )
                    
                    if response.status_code != 201:
                        logger.warning(f"Failed to index email {i+1}: Status {response.status_code}")
                        logger.warning(f"Response: {response.text}")
                        failed_indexes += 1
                    else:
                        successful_indexes += 1
                        # Track this email as indexed
                        email_id = email.get('id')
                        if email_id:
                            self.indexed_email_ids.add(email_id)
                        logger.debug(f"Successfully indexed email {i+1}/{len(emails)}")
                
                os.remove(file_path)
                
                # Add a small delay to avoid overwhelming the API
                if i > 0 and i % 10 == 0:
                    time.sleep(self.config.batch_upload_delay)  # Configurable delay
                    
            except Exception as e:
                failed_indexes += 1
                logger.error(f"Error indexing email {i+1}: {e}")
                # Clean up the temp file if it exists
                if 'file_path' in locals() and os.path.exists(file_path):
                    os.remove(file_path)
                continue

        # Update tracking information
        if successful_indexes > 0:
            self.last_indexed_hash = self._compute_email_hash(emails)
            self.last_index_time = time.time()
            # Save state to disk for persistence across requests
            self._save_state()

        indexing_total_time = time.time() - indexing_start_time
        logger.info(f"Email indexing completed: {successful_indexes} successful, {failed_indexes} failed in {indexing_total_time:.2f}s")
        if successful_indexes > 0:
            logger.info(f"📊 RAG INDEXING DETAILED PERFORMANCE: {successful_indexes} emails indexed at {successful_indexes/indexing_total_time:.1f} emails/sec")
        
        if len(emails) > 0 and successful_indexes == 0:
            raise Exception(f"Failed to index any emails. All {failed_indexes} attempts failed.")
        elif failed_indexes > 0:
            logger.warning(f"Some emails failed to index: {failed_indexes} out of {len(emails)}")
        
        if len(emails) > 0:
            print(f"Successfully indexed {successful_indexes}/{len(emails)} emails")

    def query_emails(self, query: str, k: int = 5) -> list[dict]:
        """
        Query the indexed emails to find the most relevant ones.

        Args:
            query: The user's query.
            k: The number of emails to retrieve.

        Returns:
            A list of dictionaries containing content and metadata for matching.
        """
        if not self.collection_id:
            raise Exception("Collection does not exist. Please index emails first.")

        query_start_time = time.time()
        data = {
            "collections": [self.collection_id],
            "k": k,
            "prompt": query,
            "method": "semantic"
        }
        response = self.session.post(
            url=f"{self.config.base_url}/search",
            json=data
        )
        api_time = time.time() - query_start_time
        
        response.raise_for_status()
        
        results = response.json()["data"]
        
        logger.info(f"📊 RAG QUERY DETAILED PERFORMANCE: Albert API search took {api_time:.2f}s for {k} results from query: '{query[:50]}...'")
        
        # Return both content and metadata for better matching
        formatted_results = []
        for result in results:
            chunk = result["chunk"]
            formatted_results.append({
                "content": chunk["content"],
                "metadata": chunk.get("metadata", {}),
                "score": result.get("score", 0.0),
                "document_name": chunk.get("metadata", {}).get("document_name", "")
            })
        
        return formatted_results

