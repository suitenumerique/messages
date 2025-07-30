"""
Email Service for Chatbot Integration with Multiple Search Methods.

This module provides a clean, class-based interface for email retrieval and search
for the chatbot. Supports two search methods:

1. RAG-based search (default): Uses local embeddings and vector storage
2. Contextual search: Sends emails directly to Albert API for intelligent search

Core functions:
- Get ALL emails with full metadata
- Intelligent email search using either RAG or contextual search
- Format results for mailbox display

The search method is configurable via the use_rag parameter in EmailService.__init__().
"""

import json
import re
import html
import time
import logging
from typing import Dict, List, Any
from django.contrib.auth import get_user_model


from core import models
from .rag import RAGSystem
from .contextual_search import ContextualSearchService
try:
    from .email_utils import email_utils
except ImportError as e:
    print(f"Warning: Could not import email_utils: {e}")
    email_utils = None

logger = logging.getLogger(__name__)
User = get_user_model()

# RAG system instance to be used for embedding and retrieval
rag_system = RAGSystem()

# Contextual search service instance
contextual_search_service = ContextualSearchService()


class EmailService:
    """
    Email service for chatbot integration with configurable search methods.
    
    Provides a clean interface for:
    - Email retrieval with full metadata
    - Multiple intelligent search methods (RAG and contextual)
    - Background indexing utilities
    """
    
    def __init__(self, use_rag: bool = True):
        """
        Initialize the email service.
        
        Args:
            use_rag: If True, uses RAG-based search by default. 
                    If False, uses contextual search (sending emails to Albert).
        """
        self.logger = logger
        self.use_rag = use_rag
    
    def check_user_needs_indexing(self, user_id: str) -> Dict[str, Any]:
        """
        Check if a user has emails and needs indexing, without doing the heavy work.
        
        Args:
            user_id: The user ID to check
            
        Returns:
            Dict with information about whether indexing is needed
        """
        try:
            # Check if RAG collection exists efficiently
            rag_system = RAGSystem()
            
            if rag_system.collection_exists(user_id):
                return {
                    'needs_indexing': False,
                    'has_collection': True,
                    'reason': 'Collection already exists'
                }
            
            # Quick check if user has any emails at all
            try:
                user = User.objects.get(id=user_id)
                
                # Get accessible mailboxes count
                mailboxes_count = models.Mailbox.objects.filter(
                    accesses__contact__user=user,
                    accesses__role__in=['owner', 'reader', 'admin']
                ).distinct().count()
                
                if mailboxes_count == 0:
                    return {
                        'needs_indexing': False,
                        'has_collection': False,
                        'mailboxes_count': 0,
                        'reason': 'User has no accessible mailboxes'
                    }
                
                # Quick check if user has any messages
                message_count = models.Message.objects.filter(
                    thread__accesses__contact__user=user,
                    thread__accesses__role__in=['owner', 'reader', 'admin'],
                ).count()
                
                if message_count == 0:
                    return {
                        'needs_indexing': False,
                        'has_collection': False,
                        'mailboxes_count': mailboxes_count,
                        'message_count': 0,
                        'reason': 'User has no messages'
                    }
                
                return {
                    'needs_indexing': True,
                    'has_collection': False,
                    'mailboxes_count': mailboxes_count,
                    'message_count': message_count,
                    'reason': f'User has {message_count} messages but no collection'
                }
                
            except User.DoesNotExist:
                return {
                    'needs_indexing': False,
                    'has_collection': False,
                    'reason': 'User does not exist'
                }
                
        except Exception as e:
            self.logger.error(f"Error checking indexing needs for user {user_id}: {e}")
            return {
                'needs_indexing': False,
                'has_collection': False,
                'error': str(e),
                'reason': 'Error during check'
            }

    def parse_ai_response_for_email_search(self, ai_content: str, context_emails: List[Dict]) -> List[Dict[str, Any]]:
        """
        Parse AI response for email search and return formatted results for mailbox display.
        
        Args:
            ai_content: Raw AI response content
            context_emails: List of email context objects
            
        Returns:
            List of formatted search results compatible with mailbox display
        """
        try:
            self.logger.info(f"Parsing AI response (length: {len(ai_content)} chars)")
            self.logger.info(f"AI response content: {ai_content}")  # Log full content
            
            # Create a lookup dictionary for emails by ID
            email_lookup = {email['id']: email for email in context_emails}
            self.logger.info(f"Created email lookup with {len(email_lookup)} emails")
            
            # Try to parse JSON response
            try:
                self.logger.info("Looking for JSON array in AI response...")
                # Look for JSON array in the response
                json_match = re.search(r'\[.*?\]', ai_content, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                    self.logger.info(f"Found JSON string: {json_str}")
                    ai_results = json.loads(json_str)
                    self.logger.info(f"Successfully parsed JSON with {len(ai_results)} results")
                else:
                    self.logger.warning("No JSON array found in AI response")
                    self.logger.warning(f"AI response content: {ai_content}")
                    return []
            except json.JSONDecodeError as e:
                self.logger.warning(f"Failed to parse JSON from AI response: {e}")
                self.logger.warning(f"JSON string that failed: {json_str if 'json_str' in locals() else 'No JSON string'}")
                return []
            
            # Process AI results and format for mailbox display (like Elasticsearch results)
            formatted_results = []
            for result in ai_results:
                email_id = result.get('id', '')
                if email_id in email_lookup:
                    email = email_lookup[email_id]
                    
                    # Format result like Elasticsearch search results (compatible with mailbox display)
                    formatted_result = {
                        'message_id': email_id,
                        'thread_id': email.get('thread_id', ''),
                        'subject': email.get('subject', ''),
                        'from': email.get('sender', {}).get('email', ''),  # Frontend expects 'from'
                        'sender_name': email.get('sender', {}).get('name', ''),
                        'sender_email': email.get('sender', {}).get('email', ''),
                        'date': email.get('sent_at'),  # Frontend expects 'date'
                        'sent_at': email.get('sent_at'),
                        'snippet': email.get('content', '')[:200] if email.get('content') else '',  # Frontend expects 'snippet'
                        'is_unread': email.get('flags', {}).get('is_unread', False),
                        'is_starred': email.get('flags', {}).get('is_starred', False),
                        'thread_subject': email.get('thread_subject', ''),
                        'relevance_score': result.get('relevance_score', 0.5),
                        'ai_reason': result.get('reason', 'AI identified as relevant'),
                        # Additional metadata for debugging
                        'attachment_count': email.get('attachment_count', 0),
                        'content_preview': email.get('content', '')[:200] if email.get('content') else '',
                    }
                    formatted_results.append(formatted_result)
                    self.logger.debug(f"Added result for email {email_id} with score {result.get('relevance_score', 0.5)}")
                else:
                    self.logger.warning(f"AI returned email ID {email_id} not found in context")
            
            # Sort by relevance score (highest first)
            formatted_results.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)
            
            self.logger.info(f"Successfully parsed {len(formatted_results)} email search results from AI response")
            return formatted_results
            
        except Exception as e:
            self.logger.error(f"Error parsing AI response for email search: {e}", exc_info=True)
            return []
    
    def chatbot_intelligent_email_search(
        self, 
        user_id: str, 
        user_query: str, 
        api_client,
        max_results: int = 10,
        folder: str = 'all'
    ) -> Dict[str, Any]:
        """
        Perform intelligent email search using either RAG system or contextual search.
        The method used depends on the use_rag flag set during initialization.
        
        Args:
            user_id: UUID of the user
            user_query: User's natural language search query
            api_client: Albert API client instance
            max_results: Maximum number of results to return
            folder: Folder to search in ('all', 'sent', 'draft', 'trash')
            
        Returns:
            Dictionary containing search results and metadata
        """
        self.logger.info(f"Starting intelligent email search - method: {'RAG' if self.use_rag else 'contextual'}, folder: {folder}")
        
        if self.use_rag:
            # Use RAG-based search (existing implementation)
            return self._chatbot_rag_email_search(user_id, user_query, api_client, max_results, folder)
        else:
            # Use contextual search with Albert API
            return contextual_search_service.chatbot_contextual_email_search(
                user_id, user_query, api_client, max_results
            )
    
    def _chatbot_rag_email_search(
        self, 
        user_id: str, 
        user_query: str, 
        api_client,
        max_results: int = 10,
        folder: str = 'all'
    ) -> Dict[str, Any]:
        """
        Perform intelligent email search using RAG system with embeddings.
        This method:
        1. Retrieves emails from the user's mailboxes (filtered by folder)
        2. Checks if emails are already indexed in the RAG system
        3. Indexes any new emails
        4. Uses the query to retrieve the most relevant emails
        
        Args:
            user_id: UUID of the user
            user_query: User's natural language search query
            api_client: Albert API client instance
            max_results: Maximum number of results to return
            folder: Folder to search in ('all', 'sent', 'draft', 'trash')
            
        Returns:
            Dictionary containing search results and metadata
        """
        search_start_time = time.time()
        self.logger.info(f"Starting intelligent email search with RAG - user_id: {user_id}, query: '{user_query[:100]}...', folder: '{folder}'")
        
        try:
            # Step 1: Get email context with folder filtering
            self.logger.info("Step 1: Fetching email context")
            context_start_time = time.time()
            if email_utils is None:
                self.logger.error("email_utils not available")
                return {
                    "success": False,
                    "error": "email_utils not available",
                    "results": [],
                    "search_method": "rag",
                    "total_searched": 0,
                    "processing_time": time.time() - search_start_time
                }
            context_result = email_utils.get_email_context_for_chatbot(user_id)
            context_time = time.time() - context_start_time
            
            if not context_result["success"]:
                self.logger.error(f"Failed to get email context: {context_result.get('error', 'Unknown error')}")
                return {
                    "success": False,
                    "error": f"Failed to retrieve emails: {context_result.get('error', 'Unknown error')}",
                    "results": [],
                    "search_method": "none",
                    "total_searched": 0,
                    "processing_time": time.time() - search_start_time
                }
            
            context_emails = context_result["emails"]
            self.logger.info(f"Step 1 completed: Retrieved {len(context_emails)} emails for search in {context_time:.2f}s")
            self.logger.info(f"📊 EMAIL CONTEXT PERFORMANCE: {len(context_emails)} emails retrieved in {context_time:.2f}s ({len(context_emails)/context_time:.1f} emails/sec)")
            
            if not context_emails:
                self.logger.warning("No emails available for search")
                return {
                    "success": True,
                    "results": [],
                    "message": "No emails found to search",
                    "search_method": "none",
                    "total_searched": 0,
                    "processing_time": time.time() - search_start_time
                }
            
            # Step 2: Set user-specific collection and check if it exists
            self.logger.info("Step 2: Setting user-specific RAG collection")
            rag_system.set_user_collection(user_id)
            
            if not rag_system.collection_id:
                self.logger.info("Step 2: Creating new RAG collection for user")
                rag_system.create_collection()
                self.logger.info(f"Step 2 completed: Created RAG collection: {rag_system.collection_id}")
            else:
                self.logger.info(f"Step 2: Using existing RAG collection: {rag_system.collection_id}")
                self.logger.info(f"Step 2: Found {len(rag_system.indexed_email_ids)} previously indexed emails")
            
            # Step 3: Prepare emails for indexing - format for RAG system
            self.logger.info("Step 3: Preparing emails for RAG indexing")
            emails_for_rag = []
            
            # Process ALL emails, not just a limited batch
            # Remove the batch limitation to ensure all emails are indexed
            emails_to_process = context_emails
            
            self.logger.info(f"Processing ALL {len(emails_to_process)} emails for RAG indexing")
            
            # Track attachment statistics for RAG indexing
            total_attachments_for_rag = 0
            emails_with_attachments_for_rag = 0
            
            # Keep track of email IDs to ensure we're only processing unique emails
            for email in emails_to_process:
                email_id = email['id']
                
                # Create a complete email document for RAG indexing
                # We include the subject in the body to ensure it's part of the embedding
                full_content = f"Subject: {email.get('subject', '')}\n\n"
                full_content += f"From: {email.get('sender', {}).get('name', '')} <{email.get('sender', {}).get('email', '')}>\n"
                
                # Add recipients information
                recipients = email.get('recipients', {})
                if recipients.get('to'):
                    to_emails = [f"{r.get('name', '')} <{r.get('email', '')}>".strip() for r in recipients.get('to', [])]
                    full_content += f"To: {', '.join(to_emails)}\n"
                
                if recipients.get('cc'):
                    cc_emails = [f"{r.get('name', '')} <{r.get('email', '')}>".strip() for r in recipients.get('cc', [])]
                    full_content += f"CC: {', '.join(cc_emails)}\n"
                
                # Add date information
                full_content += f"Date: {email.get('sent_at', '')}\n\n"
                
                # Add the actual content
                full_content += email.get('content', '')
                
                # Add attachment information
                if email.get('attachment_count', 0) > 0:
                    attachment_names = [att.get('name', '') for att in email.get('attachments', []) if att.get('name', '').strip()]
                    if attachment_names:
                        full_content += f"\n\nFiles attached: {', '.join(attachment_names)}"
                        self.logger.debug(f"Email {email.get('id')} RAG content includes attachments: {', '.join(attachment_names)}")
                        emails_with_attachments_for_rag += 1
                        total_attachments_for_rag += len(attachment_names)
                    else:
                        self.logger.debug(f"Email {email.get('id')} has attachment count {email.get('attachment_count')} but no valid attachment names")
                
                # Add to the RAG indexing list with metadata
                emails_for_rag.append({
                    'id': email_id,
                    'body': full_content,
                    'metadata': {
                        'subject': email.get('subject', ''),
                        'sender': email.get('sender', {}).get('email', ''),
                        'date': email.get('sent_at', ''),
                        'thread_id': email.get('thread_id', '')
                    }
                })
            
            # Log attachment statistics for RAG indexing
            self.logger.info(f"📎 RAG ATTACHMENT STATS: {emails_with_attachments_for_rag} emails with attachments out of {len(emails_to_process)} total emails")
            self.logger.info(f"📎 RAG TOTAL ATTACHMENTS: {total_attachments_for_rag} attachments across all emails for RAG indexing")
            
            self.logger.info(f"Step 3 completed: Prepared {len(emails_for_rag)} emails for RAG indexing")
            
            # Step 4: Index emails in RAG system (with smart reindexing)
            self.logger.info("Step 4: Checking if email indexing is needed")
            indexing_start_time = time.time()
            try:
                rag_system.index_emails(emails_for_rag)
                indexing_time = time.time() - indexing_start_time
                self.logger.info(f"Step 4 completed: Email indexing process finished in {indexing_time:.2f}s")
                self.logger.info(f"📊 RAG INDEXING PERFORMANCE: {len(emails_for_rag)} emails indexed in {indexing_time:.2f}s ({len(emails_for_rag)/indexing_time:.1f} emails/sec)")
            except Exception as index_error:
                indexing_time = time.time() - indexing_start_time
                self.logger.error(f"Failed to index emails in RAG system: {index_error}", exc_info=True)
                
                # Check if this is a complete failure or partial failure
                if "Failed to index any emails" in str(index_error):
                    # Complete failure - return error
                    return {
                        "success": False,
                        "error": f"Failed to index emails: {str(index_error)}",
                        "results": [],
                        "search_method": "rag_error",
                        "total_searched": len(context_emails),
                        "processing_time": time.time() - search_start_time
                    }
                else:
                    # Partial failure - log warning but continue
                    self.logger.warning(f"Some emails failed to index, but continuing with partial collection: {index_error}")
                    # Continue to query step
            
            # Step 5: Query the RAG system with the user's query
            self.logger.info(f"Step 5: Querying RAG system with user query: '{user_query}'")
            query_time = 0.0  # Initialize query_time for error cases
            try:
                query_start_time = time.time()
                relevant_contents = rag_system.query_emails(user_query, k=max_results)
                query_time = time.time() - query_start_time
                
                self.logger.info(f"Step 5 completed: Retrieved {len(relevant_contents)} relevant emails in {query_time:.2f}s")
                self.logger.info(f"📊 RAG QUERY PERFORMANCE: {len(relevant_contents)} results retrieved in {query_time:.2f}s from {len(context_emails)} total emails")
                
                # Log detailed scores for all retrieved emails
                if relevant_contents:
                    self.logger.info("=== RAG RETRIEVAL SCORES ===")
                    for i, result in enumerate(relevant_contents):
                        score = result.get("score", 0.0)
                        document_name = result.get("metadata", {}).get("document_name", "unknown")
                        content_preview = result.get("content", "")[:100].replace('\n', ' ')
                        self.logger.info(f"  #{i+1:2d}: Score {score:.4f} | {document_name} | Preview: {content_preview}...")
                    
                    scores = [result.get("score", 0.0) for result in relevant_contents]
                    avg_score = sum(scores) / len(scores)
                    min_score = min(scores)
                    max_score = max(scores)
                    self.logger.info(f"Score Statistics: Max={max_score:.4f}, Min={min_score:.4f}, Avg={avg_score:.4f}")
                    self.logger.info("=== END RAG SCORES ===")
                
                if not relevant_contents:
                    self.logger.warning("RAG query returned no results")
                    return {
                        "success": True,
                        "results": [],
                        "message": "No relevant emails found",
                        "search_method": "rag",
                        "total_searched": len(context_emails),
                        "processing_time": time.time() - search_start_time
                    }
                
                # Step 6: Match RAG results to original emails and format for frontend
                self.logger.info("Step 6: Matching RAG results to original emails")
                
                # Create email lookup by ID
                email_lookup = {email['id']: email for email in context_emails}
                
                # Calculate dynamic score threshold based on highest score
                if relevant_contents:
                    scores = [result.get("score", 0.0) for result in relevant_contents]
                    highest_score = max(scores) if scores else 0.0
                    dynamic_threshold = highest_score * rag_system.config.min_relevance_score_percentage
                    self.logger.info(f"Dynamic threshold: {dynamic_threshold:.3f} ({rag_system.config.min_relevance_score_percentage:.1%} of highest score {highest_score:.3f})")
                else:
                    dynamic_threshold = 0.0
                
                # Format results for frontend
                formatted_results = []
                
                for i, result in enumerate(relevant_contents):
                    content = result.get("content", "")
                    metadata = result.get("metadata", {})
                    document_name = metadata.get("document_name", "")
                    score = result.get("score", 0.0)
                    
                    matched_email_id = None
                    
                    # Method 1: Extract email ID from document filename
                    # The filename format is email_{i}_{email_id}.txt
                    if document_name:
                        self.logger.debug(f"Trying to match document: {document_name}")
                        # Extract email ID from filename like "email_5_12345abc-def0-1234-abcd-123456789abc.txt"
                        if document_name.startswith("email_") and document_name.endswith(".txt"):
                            # Remove the .txt extension and split by underscore
                            base_name = document_name.replace('.txt', '')
                            parts = base_name.split('_')
                            if len(parts) >= 3:
                                # The email ID should be everything after the second underscore
                                # In case the email ID itself contains underscores
                                potential_email_id = '_'.join(parts[2:])
                                if potential_email_id in email_lookup:
                                    matched_email_id = potential_email_id
                                    self.logger.debug(f"✅ Matched email via filename: {document_name} -> {matched_email_id}")
                                else:
                                    self.logger.debug(f"❌ Email ID '{potential_email_id}' from filename not found in lookup")
                            else:
                                self.logger.debug(f"❌ Filename format unexpected: {document_name}")
                        else:
                            self.logger.debug(f"❌ Filename doesn't match expected pattern: {document_name}")
                    
                    # Method 2: Search for email ID directly in the metadata  
                    if not matched_email_id and metadata:
                        # Check if there's an email ID in the metadata
                        if 'email_id' in metadata and metadata['email_id'] in email_lookup:
                            matched_email_id = metadata['email_id']
                            self.logger.debug(f"✅ Matched email via metadata: {matched_email_id}")
                    
                    # Method 3: Search for email ID in content
                    if not matched_email_id:
                        for email_id in email_lookup.keys():
                            if email_id in content:
                                matched_email_id = email_id
                                self.logger.debug(f"✅ Matched email via content search: {matched_email_id}")
                                break
                    
                    # Method 4: Fuzzy matching based on subject and sender
                    if not matched_email_id:
                        best_match_id = None
                        best_match_score = 0
                        
                        # Extract subject from content (it should be at the beginning)
                        content_lines = content.split('\n')
                        content_subject = ""
                        content_sender = ""
                        
                        for line in content_lines[:5]:  # Check first few lines
                            if line.startswith("Subject: "):
                                content_subject = line.replace("Subject: ", "").strip()
                            elif line.startswith("From: "):
                                content_sender = line.replace("From: ", "").strip()
                        
                        if content_subject or content_sender:
                            for email_id, email in email_lookup.items():
                                match_score = 0
                                
                                # Score based on subject similarity
                                if content_subject and email.get('subject'):
                                    subject_words_content = set(content_subject.lower().split())
                                    subject_words_email = set(email.get('subject', '').lower().split())
                                    subject_overlap = len(subject_words_content & subject_words_email)
                                    if subject_overlap > 0:
                                        match_score += subject_overlap * 10
                                
                                # Score based on sender similarity
                                if content_sender and email.get('sender', {}).get('email'):
                                    if email.get('sender', {}).get('email').lower() in content_sender.lower():
                                        match_score += 20
                                
                                if match_score > best_match_score:
                                    best_match_score = match_score
                                    best_match_id = email_id
                        
                        if best_match_score > 15:  # Threshold for a good match
                            matched_email_id = best_match_id
                            self.logger.debug(f"✅ Matched email via fuzzy matching: {matched_email_id} (score: {best_match_score})")
                    
                    if matched_email_id and matched_email_id in email_lookup:
                        email = email_lookup[matched_email_id]
                        
                        # Use the Albert API score if available, otherwise calculate based on position
                        relevance_score = score if score > 0 else (1.0 - (i / max(len(relevant_contents), 1)))
                        
                        # Apply dynamic score threshold filtering - only include results above percentage of highest score
                        if relevance_score < dynamic_threshold:
                            self.logger.info(f"🚫 FILTERED: Email {matched_email_id[:8]}... | Score: {relevance_score:.4f} < Threshold: {dynamic_threshold:.4f} | Subject: {email.get('subject', 'No subject')[:50]}...")
                            continue
                        
                        self.logger.info(f"✅ INCLUDED: Email {matched_email_id[:8]}... | Score: {relevance_score:.4f} ≥ Threshold: {dynamic_threshold:.4f} | Subject: {email.get('subject', 'No subject')[:50]}...")
                        
                        # Format result for frontend compatibility
                        formatted_result = {
                            'id': matched_email_id,  # Frontend expects 'id'
                            'message_id': matched_email_id,
                            'thread_id': email.get('thread_id', ''),
                            'subject': email.get('subject', ''),
                            'from': email.get('sender', {}).get('email', ''),  # Frontend expects 'from'
                            'sender': {  # Frontend expects 'sender' object
                                'email': email.get('sender', {}).get('email', ''),
                                'name': email.get('sender', {}).get('name', '')
                            },
                            'sender_name': email.get('sender', {}).get('name', ''),
                            'sender_email': email.get('sender', {}).get('email', ''),
                            'date': email.get('sent_at'),  # Frontend expects 'date'
                            'sent_at': email.get('sent_at'),
                            'snippet': email.get('content', '')[:200] if email.get('content') else '',  # Frontend expects 'snippet'
                            'is_unread': email.get('flags', {}).get('is_unread', False),
                            'is_starred': email.get('flags', {}).get('is_starred', False),
                            'thread_subject': email.get('thread_subject', ''),
                            'relevance_score': relevance_score,
                            'ai_reason': f"Semantically relevant to query: '{user_query}' (score: {relevance_score:.3f}, threshold: {dynamic_threshold:.3f})",
                            # Additional metadata for debugging
                            'attachment_count': email.get('attachment_count', 0),
                            'content_preview': email.get('content', '')[:200] if email.get('content') else '',
                            'search_method': 'rag'
                        }
                        formatted_results.append(formatted_result)
                        self.logger.debug(f"Added result for email {matched_email_id} with score {relevance_score:.3f}")
                    else:
                        self.logger.warning(f"❌ Could not match RAG result to an email in context")
                        self.logger.warning(f"📝 Content preview: {content[:200]}...")
                        self.logger.warning(f"📁 Document name: {document_name}")
                        self.logger.warning(f"🏷️ Metadata: {metadata}")
                        # Log available email IDs for debugging
                        self.logger.warning(f"📋 Available email IDs (first 5): {list(email_lookup.keys())[:5]}...")
                        
                        # Try to extract any UUID-like pattern from content
                        import re
                        uuid_pattern = r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}'
                        found_uuids = re.findall(uuid_pattern, content)
                        if found_uuids:
                            self.logger.warning(f"🔍 Found UUIDs in content: {found_uuids[:3]}...")
                            for uuid in found_uuids:
                                if uuid in email_lookup:
                                    self.logger.warning(f"🎯 UUID {uuid} found in email lookup! This should have been matched.")
                                    break
                
                # Deduplicate results by email ID to avoid duplicate emails
                original_count = len(formatted_results)
                seen_ids = set()
                deduplicated_results = []
                for result in formatted_results:
                    email_id = result.get('id')
                    if email_id and email_id not in seen_ids:
                        seen_ids.add(email_id)
                        deduplicated_results.append(result)
                
                if original_count != len(deduplicated_results):
                    self.logger.info(f"🔍 RAG DEDUPLICATION: Removed {original_count - len(deduplicated_results)} duplicate results")
                    self.logger.info(f"   • Before deduplication: {original_count} results")
                    self.logger.info(f"   • After deduplication: {len(deduplicated_results)} unique results")
                
                formatted_results = deduplicated_results
                
                # Log filtering summary
                total_rag_results = len(relevant_contents)
                filtered_count = total_rag_results - len(formatted_results)
                self.logger.info("=== FILTERING SUMMARY ===")
                self.logger.info(f"📊 RAG Retrieved: {total_rag_results} emails")
                self.logger.info(f"✅ Passed Filter: {len(formatted_results)} emails") 
                self.logger.info(f"🚫 Filtered Out: {filtered_count} emails")
                self.logger.info(f"📈 Filter Rate: {(filtered_count/total_rag_results*100):.1f}% removed")
                self.logger.info(f"🎯 Threshold Used: {dynamic_threshold:.4f} ({rag_system.config.min_relevance_score_percentage:.0%} of {max([result.get('score', 0.0) for result in relevant_contents]):.4f})")
                if formatted_results:
                    final_scores = [r.get('relevance_score', 0) for r in formatted_results]
                    self.logger.info(f"📋 Final Score Range: {min(final_scores):.4f} - {max(final_scores):.4f}")
                self.logger.info("=== END FILTERING ===")
                
                self.logger.info(f"Step 6 completed: Matched and formatted {len(formatted_results)} email results (after dynamic score filtering with threshold {dynamic_threshold:.3f})")
                
                search_time = time.time() - search_start_time
                self.logger.info(f"📊 RAG TOTAL PERFORMANCE SUMMARY:")
                self.logger.info(f"   • Total Search Time: {search_time:.2f}s")
                self.logger.info(f"   • Email Context Time: {context_time:.2f}s ({(context_time/search_time*100):.1f}%)")
                self.logger.info(f"   • Indexing Time: {indexing_time:.2f}s ({(indexing_time/search_time*100):.1f}%)")
                self.logger.info(f"   • Query Time: {query_time:.2f}s ({(query_time/search_time*100):.1f}%)")
                self.logger.info(f"   • Processing Time: {(search_time-context_time-indexing_time-query_time):.2f}s ({((search_time-context_time-indexing_time-query_time)/search_time*100):.1f}%)")
                self.logger.info(f"   • Emails Processed: {len(context_emails)}")
                self.logger.info(f"   • Results Returned: {len(formatted_results)}")
                
                return {
                    "success": True,
                    "results": formatted_results,
                    "search_method": "rag",
                    "total_searched": len(context_emails),
                    "total_matched": len(relevant_contents),
                    "results_after_filtering": len(formatted_results),
                    "dynamic_score_threshold": dynamic_threshold,
                    "highest_score": max([result.get("score", 0.0) for result in relevant_contents]) if relevant_contents else 0.0,
                    "score_threshold_percentage": rag_system.config.min_relevance_score_percentage,
                    "processing_time": search_time
                }
                
            except Exception as query_error:
                self.logger.error(f"Failed to query RAG system: {query_error}", exc_info=True)
                return {
                    "success": False,
                    "error": f"Failed to query emails: {str(query_error)}",
                    "results": [],
                    "search_method": "rag_error",
                    "total_searched": len(context_emails),
                    "processing_time": time.time() - search_start_time
                }
            
        except Exception as e:
            total_search_time = time.time() - search_start_time
            self.logger.error(f"Error in chatbot_intelligent_email_search with RAG: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Search failed: {str(e)}",
                "results": [],
                "search_method": "error",
                "total_searched": 0,
                "processing_time": total_search_time
            }