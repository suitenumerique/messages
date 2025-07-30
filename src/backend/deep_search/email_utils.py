"""
Email utilities for chatbot integration.

This module provides common email retrieval and parsing functions that can be shared
between different email service implementations (RAG and contextual search).
"""

import re
import html
import time
import logging
from typing import Dict, List, Any
from django.contrib.auth import get_user_model

from core import models

logger = logging.getLogger(__name__)
User = get_user_model()


class EmailUtils:
    """Utility class for common email operations."""
    
    def __init__(self):
        """Initialize the email utilities."""
        self.logger = logger
    
    def get_user_accessible_mailboxes(self, user_id: str) -> List[models.Mailbox]:
        """
        Get all mailboxes that a user has access to.
        
        Args:
            user_id: UUID of the user
            
        Returns:
            List of Mailbox objects the user can access
        """
        try:
            self.logger.info(f"Getting accessible mailboxes for user_id: {user_id}")
            
            user = User.objects.get(id=user_id)
            self.logger.debug(f"Found user: {user.email if hasattr(user, 'email') else user}")
            
            mailboxes = models.Mailbox.objects.filter(
                accesses__user=user
            ).select_related('domain', 'contact').order_by("-created_at")
            
            mailbox_count = mailboxes.count()
            self.logger.info(f"Found {mailbox_count} accessible mailboxes for user {user}")
            
            if mailbox_count > 0:
                mailbox_list = list(mailboxes)
                self.logger.debug(f"Mailbox IDs: {[str(mb.id) for mb in mailbox_list[:5]]}")
                return mailbox_list
            else:
                self.logger.warning(f"No accessible mailboxes found for user {user}")
                return []
                
        except User.DoesNotExist:
            self.logger.error(f"User with ID {user_id} not found")
            return []
        except Exception as e:
            self.logger.error(f"Error retrieving mailboxes for user {user_id}: {e}", exc_info=True)
            return []
    
    def get_recent_messages(self, mailboxes: List[models.Mailbox], limit: int = None) -> List[models.Message]:
        """
        Get messages from the given mailboxes.
        
        Args:
            mailboxes: List of Mailbox objects
            limit: Maximum number of messages to retrieve (default: None for all messages)
            
        Returns:
            List of Message objects
        """
        try:
            limit_text = f"limit: {limit}" if limit else "all messages"
            self.logger.info(f"Getting messages from {len(mailboxes)} mailboxes, {limit_text}")
            
            if not mailboxes:
                self.logger.warning("No mailboxes provided to get_recent_messages")
                return []
            
            mailbox_ids = [mb.id for mb in mailboxes]
            self.logger.debug(f"Searching messages in mailbox IDs: {mailbox_ids[:5]}")
            
            messages_query = models.Message.objects.filter(
                thread__accesses__mailbox__id__in=mailbox_ids,
                is_draft=False,
                is_trashed=False
            ).select_related(
                'sender', 'thread'
            ).prefetch_related(
                'attachments',
                'attachments__blob'
            ).order_by('-created_at')
            
            # Log attachment counts for debugging
            self.logger.info(f"Checking attachment counts in database query...")
            total_messages_with_attachments = models.Message.objects.filter(
                thread__accesses__mailbox__id__in=mailbox_ids,
                is_draft=False,
                is_trashed=False,
                attachments__isnull=False
            ).distinct().count()
            self.logger.info(f"Total messages with attachments in DB: {total_messages_with_attachments}")
            
            total_attachments_count = models.Attachment.objects.filter(
                messages__thread__accesses__mailbox__id__in=mailbox_ids,
                messages__is_draft=False,
                messages__is_trashed=False
            ).count()
            self.logger.info(f"Total attachments in DB for these messages: {total_attachments_count}")
            
            # Apply limit only if specified
            if limit is not None:
                messages_query = messages_query[:limit]
            
            message_list = list(messages_query)
            message_count = len(message_list)
            
            # Additional debugging: Check attachment relationships
            if message_count > 0:
                sample_msg = message_list[0]
                self.logger.debug(f"Sample message {sample_msg.id} attachment count check:")
                self.logger.debug(f"  - .attachments.all().count(): {sample_msg.attachments.all().count()}")
                self.logger.debug(f"  - .attachments.exists(): {sample_msg.attachments.exists()}")
                
                # Check if there are attachments in the mailboxes at all
                attachment_check = models.Attachment.objects.filter(
                    mailbox__id__in=mailbox_ids
                ).count()
                self.logger.info(f"Total attachments in these mailboxes: {attachment_check}")
            
            self.logger.info(f"Retrieved {message_count} messages from {len(mailboxes)} mailboxes")
            
            if message_count > 0:
                first_msg = message_list[0]
                last_msg = message_list[-1]
                self.logger.debug(f"Date range: {last_msg.created_at} to {first_msg.created_at}")
                self.logger.debug(f"Sample message IDs: {[str(msg.id) for msg in message_list[:3]]}")
            else:
                self.logger.warning("No messages found in the provided mailboxes")
            
            return message_list
            
        except Exception as e:
            self.logger.error(f"Error retrieving recent messages: {e}", exc_info=True)
            return []
    
    def get_parsed_message_content(self, message: models.Message) -> str:
        """
        Get parsed text content from a message for chatbot processing.
        
        Args:
            message: Message object
            
        Returns:
            String containing the parsed text content
        """
        try:
            self.logger.debug(f"Parsing message {message.id} - {message.subject}")
            
            parsed_data = message.get_parsed_data()
            self.logger.debug(f"Parsed data keys: {list(parsed_data.keys())}")
            
            text_content = ""
            text_body = parsed_data.get('textBody', [])
            html_body = parsed_data.get('htmlBody', [])
            self.logger.debug(f"Text body parts: {len(text_body)}, HTML body parts: {len(html_body)}")
            
            if text_body:
                for text_part in text_body:
                    if isinstance(text_part, dict) and 'content' in text_part:
                        text_content += text_part['content'] + "\n"
                    elif isinstance(text_part, str):
                        text_content += text_part + "\n"
            
            if not text_content and html_body:
                self.logger.debug(f"No text body, extracting from HTML")
                for html_part in html_body:
                    if isinstance(html_part, dict) and 'content' in html_part:
                        html_content = html_part['content']
                        clean_text = re.sub(r'<[^>]+>', '', html_content)
                        clean_text = html.unescape(clean_text)
                        text_content += clean_text + "\n"
            
            text_content = text_content.strip()
            
            self.logger.debug(f"Extracted {len(text_content)} characters of text content")
            return text_content
            
        except Exception as e:
            self.logger.error(f"Error parsing message content for {message}: {e}", exc_info=True)
            return ""
    
    def get_parsed_message_details(self, message: models.Message) -> Dict[str, Any]:
        """
        Get parsed content details from a message using the model's built-in parsing methods.
        
        Args:
            message: Message object
            
        Returns:
            Dictionary with parsed message content and metadata
        """
        try:
            self.logger.debug(f"Parsing message details {message.id} - {message.subject}")
            
            # Check if message has blob with attachments data
            if hasattr(message, 'blob') and message.blob:
                self.logger.debug(f"Message {message.id} has blob: {message.blob.id}")
            
            parsed_data = message.get_parsed_data()
            self.logger.debug(f"Parsed data keys: {list(parsed_data.keys())}")
            
            # Check if parsed data contains attachment information
            parsed_attachments = parsed_data.get('attachments', [])
            if parsed_attachments:
                self.logger.debug(f"Found {len(parsed_attachments)} attachments in parsed data")
            
            text_body = parsed_data.get('textBody', [])
            html_body = parsed_data.get('htmlBody', [])
            self.logger.debug(f"Text body parts: {len(text_body)}, HTML body parts: {len(html_body)}")
            
            attachments = []
            attachment_queryset = message.attachments.all()
            attachment_count_raw = attachment_queryset.count()
            self.logger.debug(f"Message {message.id}: Raw attachment count from queryset: {attachment_count_raw}")
            
            # If no attachments from the relationship, check if they're in parsed data
            if attachment_count_raw == 0 and parsed_attachments:
                self.logger.warning(f"Message {message.id}: No attachments in relationship but {len(parsed_attachments)} in parsed data")
                # Try to use parsed attachments as fallback
                for parsed_att in parsed_attachments:
                    attachments.append({
                        'name': parsed_att.get('filename', parsed_att.get('name', 'Unknown')),
                        'size': parsed_att.get('size', 0),
                        'content_type': parsed_att.get('content_type', parsed_att.get('contentType', 'application/octet-stream')),
                        'sha256': None,
                        'source': 'parsed_data'
                    })
            else:
                # Use the relationship-based attachments
                for attachment in attachment_queryset:
                    try:
                        attachment_data = {
                            'name': attachment.name,
                            'size': attachment.size,
                            'content_type': attachment.content_type,
                            'sha256': attachment.sha256.hex() if attachment.sha256 else None,
                            'source': 'relationship'
                        }
                        attachments.append(attachment_data)
                        self.logger.debug(f"Attachment: name='{attachment.name}', size={attachment.size}, type='{attachment.content_type}'")
                    except Exception as e:
                        self.logger.error(f"Error retrieving attachment data for {attachment}: {e}", exc_info=True)
                        # Fallback data in case of error
                        attachments.append({
                            'name': getattr(attachment, 'name', 'Unknown'),
                            'size': 0,
                            'content_type': 'application/octet-stream',
                            'sha256': None,
                            'error': str(e),
                            'source': 'relationship_error'
                        })
            
            # Additional debugging: Check has_attachments flag vs actual attachments
            if hasattr(message, 'has_attachments'):
                flag_has_attachments = message.has_attachments
                actual_has_attachments = len(attachments) > 0
                if flag_has_attachments != actual_has_attachments:
                    self.logger.warning(f"Message {message.id}: has_attachments flag ({flag_has_attachments}) doesn't match actual attachments ({actual_has_attachments})")
            
            if attachment_count_raw != len(attachments):
                self.logger.warning(f"Message {message.id}: Attachment count mismatch! Raw count: {attachment_count_raw}, Processed count: {len(attachments)}")
                
            self.logger.debug(f"Found {len(attachments)} attachments for message {message.id}")
            if attachments:
                attachment_names = [att.get('name', 'unnamed') for att in attachments]
                self.logger.debug(f"Attachment names: {attachment_names}")
            
            recipients_by_type = message.get_all_recipient_contacts()
            recipients = {
                'to': [{'name': contact.name, 'email': contact.email} 
                       for contact in recipients_by_type.get('to', [])],
                'cc': [{'name': contact.name, 'email': contact.email} 
                       for contact in recipients_by_type.get('cc', [])],
                'bcc': [{'name': contact.name, 'email': contact.email} 
                        for contact in recipients_by_type.get('bcc', [])]
            }
            self.logger.debug(f"Recipients - To: {len(recipients['to'])}, CC: {len(recipients['cc'])}, BCC: {len(recipients['bcc'])}")
            
            result = {
                'subject': message.subject,
                'sender': {
                    'name': message.sender.name,
                    'email': message.sender.email
                },
                'sent_at': message.sent_at,
                'text_body': text_body,
                'html_body': html_body,
                'attachments': attachments,
                'recipients': recipients,
                'is_draft': message.is_draft,
                'is_unread': message.is_unread,
                'is_starred': message.is_starred,
                'thread_id': str(message.thread.id),
                'message_id': str(message.id),
            }
            
            self.logger.debug(f"Successfully parsed message {message.id}")
            return result
            
        except Exception as e:
            self.logger.error(f"Error parsing message content for {message}: {e}", exc_info=True)
            return {
                'subject': message.subject,
                'error': str(e)
            }
    
    def get_email_context_for_chatbot(self, user_id: str) -> Dict[str, Any]:
        """
        Main function to gather email context for the chatbot.
        Gets ALL emails with all available information.
        
        Args:
            user_id: UUID of the user
            
        Returns:
            Dictionary containing email context and metadata
        """
        start_time = time.time()
        self.logger.info(f"Getting email context for chatbot - user_id: {user_id}")
        
        try:
            # Step 1: Get accessible mailboxes
            self.logger.info("Step 1: Getting accessible mailboxes")
            mailboxes = self.get_user_accessible_mailboxes(user_id)
            
            if not mailboxes:
                self.logger.warning(f"No accessible mailboxes found for user {user_id}")
                return {
                    "success": False,
                    "error": "No accessible mailboxes found",
                    "emails": [],
                    "total_emails": 0,
                    "mailbox_count": 0,
                    "processing_time": time.time() - start_time
                }
            
            self.logger.info(f"Step 1 completed: Found {len(mailboxes)} accessible mailboxes")
            
            # Step 2: Get recent messages with full details
            self.logger.info("Step 2: Getting ALL messages with full details")
            recent_message_objects = self.get_recent_messages(mailboxes, limit=None)
            
            if not recent_message_objects:
                self.logger.warning(f"No messages found in mailboxes for user {user_id}")
                return {
                    "success": False,
                    "error": "No messages found",
                    "emails": [],
                    "total_emails": 0,
                    "mailbox_count": len(mailboxes),
                    "processing_time": time.time() - start_time
                }
            
            self.logger.info(f"Step 2 completed: Retrieved {len(recent_message_objects)} messages")
            
            # Step 3: Extract complete email information
            self.logger.info("Step 3: Extracting complete email information")
            emails_with_full_info = []
            
            for i, message in enumerate(recent_message_objects):
                if i < 10:
                    self.logger.debug(f"Processing message {i+1}: ID={message.id}, subject={message.subject[:50]}...")
                
                parsed_details = self.get_parsed_message_details(message)
                text_content = self.get_parsed_message_content(message)
                
                # Use attachments from parsed_details to avoid duplication
                attachments = parsed_details.get('attachments', [])
                
                # Log attachment information for debugging
                if attachments:
                    attachment_names = [att.get('name', 'unnamed') for att in attachments]
                    self.logger.info(f"Message {message.id} has {len(attachments)} attachments: {attachment_names}")
                else:
                    self.logger.debug(f"Message {message.id} has no attachments")
                
                email_data = {
                    "id": str(message.id),
                    "subject": message.subject,
                    "content": text_content,
                    "sender": {
                        "name": message.sender.name,
                        "email": message.sender.email
                    },
                    "recipients": parsed_details.get('recipients', {}),
                    "sent_at": message.sent_at.isoformat() if message.sent_at else None,
                    "created_at": message.created_at.isoformat(),
                    "thread_id": str(message.thread.id),
                    "thread_subject": message.thread.subject,
                    "attachments": attachments,
                    "attachment_count": len(attachments),
                    "flags": {
                        "is_unread": message.is_unread,
                        "is_starred": message.is_starred,
                        "is_draft": message.is_draft,
                        "is_trashed": message.is_trashed,
                        "is_spam": message.is_spam,
                        "is_archived": message.is_archived,
                        "is_sender": message.is_sender
                    }
                }
                
                emails_with_full_info.append(email_data)
            
            # Log attachment summary
            total_attachments_found = sum(len(email.get('attachments', [])) for email in emails_with_full_info)
            emails_with_attachments = sum(1 for email in emails_with_full_info if email.get('attachments'))
            self.logger.info(f"ATTACHMENT SUMMARY: {emails_with_attachments} emails with attachments out of {len(emails_with_full_info)} total emails")
            self.logger.info(f"ATTACHMENT SUMMARY: {total_attachments_found} total attachments found across all emails")
            
            if emails_with_attachments > 0:
                sample_attachments = []
                for email in emails_with_full_info[:10]:  # Check first 10 emails
                    if email.get('attachments'):
                        for att in email['attachments'][:3]:  # Show first 3 attachments per email
                            sample_attachments.append(f"{email['id'][:8]}...:{att.get('name', 'unnamed')}")
                self.logger.info(f"SAMPLE ATTACHMENTS: {sample_attachments}")
            
            self.logger.info(f"Step 3 completed: Processed {len(emails_with_full_info)} emails with full information")
            
            processing_time = time.time() - start_time
            
            result = {
                "success": True,
                "emails": emails_with_full_info,
                "total_emails": len(emails_with_full_info),
                "mailbox_count": len(mailboxes),
                "processing_time": processing_time
            }
            
            self.logger.info(f"Email context retrieval completed successfully: "
                           f"{len(emails_with_full_info)} emails retrieved, "
                           f"processing time: {processing_time:.2f}s")
            
            return result
            
        except Exception as e:
            processing_time = time.time() - start_time
            self.logger.error(f"Error in get_email_context_for_chatbot for user {user_id}: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Error retrieving email context: {str(e)}",
                "emails": [],
                "total_emails": 0,
                "mailbox_count": 0,
                "processing_time": processing_time
            }


# Create a singleton instance of the email utilities
email_utils = EmailUtils()
