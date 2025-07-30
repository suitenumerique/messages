#!/usr/bin/env python3
"""
Django management command to repair broken Many-to-Many relationships between Messages and Attachments.

This script fixes the issue where:
- Attachments exist in the database
- Messages have has_attachments=True flag set
- But the M2M intermediate table is empty, so message.attachments.all() returns nothing

Usage:
    python manage.py repair_attachments [--dry-run] [--mailbox-id UUID]
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from core.models import Message, Attachment, Mailbox
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Repair broken Many-to-Many relationships between Messages and Attachments'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes',
        )
        parser.add_argument(
            '--mailbox-id',
            type=str,
            help='Only repair attachments for a specific mailbox (UUID)',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Enable verbose logging',
        )

    def handle(self, *args, **options):
        """Main command handler."""
        
        # Set up logging
        if options['verbose']:
            logging.basicConfig(level=logging.INFO)
        
        dry_run = options['dry_run']
        mailbox_id = options['mailbox_id']
        
        self.stdout.write("=" * 60)
        self.stdout.write("🔧 ATTACHMENT REPAIR SCRIPT")
        self.stdout.write("=" * 60)
        
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No changes will be made"))
        
        try:
            # Step 1: Analyze the problem
            self.stdout.write("\n📊 ANALYZING ATTACHMENT PROBLEM...")
            analysis = self.analyze_attachment_problem(mailbox_id)
            self.print_analysis(analysis)
            
            if not analysis['attachments']:
                self.stdout.write(self.style.ERROR("❌ No attachments found to repair"))
                return
            
            if not analysis['messages_with_flag']:
                self.stdout.write(self.style.ERROR("❌ No messages with has_attachments=True found"))
                return
            
            # Step 2: Create repair plan
            self.stdout.write("\n🔍 CREATING REPAIR PLAN...")
            repair_plan = self.create_repair_plan(analysis)
            self.print_repair_plan(repair_plan)
            
            if not repair_plan['links_to_create']:
                self.stdout.write(self.style.WARNING("⚠️ No attachment links need to be created"))
                return
            
            # Step 3: Execute repairs
            if not dry_run:
                self.stdout.write("\n🔧 EXECUTING REPAIRS...")
                results = self.execute_repairs(repair_plan)
                self.print_results(results)
            else:
                self.stdout.write(self.style.WARNING("\n⚠️ DRY RUN - Repairs not executed"))
            
            self.stdout.write(self.style.SUCCESS("\n✅ Attachment repair completed!"))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error during repair: {e}"))
            raise CommandError(f"Repair failed: {e}")

    def analyze_attachment_problem(self, mailbox_id: str = None) -> Dict[str, Any]:
        """Analyze the current state of attachments and messages."""
        
        # Filter by mailbox if specified
        attachment_filter = {}
        message_filter = {}
        
        if mailbox_id:
            try:
                mailbox = Mailbox.objects.get(id=mailbox_id)
                attachment_filter['mailbox'] = mailbox
                message_filter['thread__accesses__mailbox'] = mailbox
                self.stdout.write(f"🎯 Filtering by mailbox: {mailbox}")
            except Mailbox.DoesNotExist:
                raise CommandError(f"Mailbox {mailbox_id} not found")
        
        # Get all attachments
        attachments = list(Attachment.objects.filter(**attachment_filter).select_related('mailbox', 'blob'))
        
        # Get messages with has_attachments=True
        messages_with_flag = list(Message.objects.filter(
            has_attachments=True,
            **message_filter
        ).select_related('thread', 'sender').prefetch_related('attachments'))
        
        # Count current M2M links
        total_m2m_links = 0
        orphaned_attachments = []
        
        for attachment in attachments:
            linked_messages_count = attachment.messages.count()
            total_m2m_links += linked_messages_count
            
            if linked_messages_count == 0:
                orphaned_attachments.append(attachment)
        
        return {
            'attachments': attachments,
            'messages_with_flag': messages_with_flag,
            'orphaned_attachments': orphaned_attachments,
            'total_m2m_links': total_m2m_links,
            'mailbox_filter': mailbox_id
        }

    def print_analysis(self, analysis: Dict[str, Any]):
        """Print the analysis results."""
        
        self.stdout.write(f"📊 CURRENT STATE:")
        self.stdout.write(f"   • Total attachments: {len(analysis['attachments'])}")
        self.stdout.write(f"   • Messages with has_attachments=True: {len(analysis['messages_with_flag'])}")
        self.stdout.write(f"   • Orphaned attachments (no M2M links): {len(analysis['orphaned_attachments'])}")
        self.stdout.write(f"   • Total M2M links: {analysis['total_m2m_links']}")
        
        if analysis['mailbox_filter']:
            self.stdout.write(f"   • Filtered by mailbox: {analysis['mailbox_filter']}")
        
        # Show sample orphaned attachments
        if analysis['orphaned_attachments']:
            self.stdout.write(f"\n🔍 ORPHANED ATTACHMENTS:")
            for att in analysis['orphaned_attachments'][:5]:
                self.stdout.write(f"   • {att.name} (ID: {att.id}, Mailbox: {att.mailbox})")
                if len(analysis['orphaned_attachments']) > 5:
                    remaining = len(analysis['orphaned_attachments']) - 5
                    self.stdout.write(f"   • ... and {remaining} more")
                    break

    def create_repair_plan(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Create a plan for repairing the M2M relationships."""
        
        links_to_create = []
        
        # For each orphaned attachment, find candidate messages in the same mailbox
        for attachment in analysis['orphaned_attachments']:
            # Find messages in the same mailbox that have has_attachments=True
            candidate_messages = [
                msg for msg in analysis['messages_with_flag']
                if any(access.mailbox == attachment.mailbox for access in msg.thread.accesses.all())
            ]
            
            if candidate_messages:
                # Strategy: Link to the most recent message with has_attachments=True
                # This is a reasonable heuristic since attachments are usually linked to recent messages
                most_recent_message = max(candidate_messages, key=lambda m: m.created_at)
                
                links_to_create.append({
                    'attachment': attachment,
                    'message': most_recent_message,
                    'reasoning': f"Most recent message with has_attachments=True in mailbox {attachment.mailbox}"
                })
        
        return {
            'links_to_create': links_to_create,
            'strategy': 'Link orphaned attachments to most recent message with has_attachments=True in same mailbox'
        }

    def print_repair_plan(self, repair_plan: Dict[str, Any]):
        """Print the repair plan."""
        
        self.stdout.write(f"🔧 REPAIR STRATEGY: {repair_plan['strategy']}")
        self.stdout.write(f"📝 PLANNED ACTIONS:")
        self.stdout.write(f"   • M2M links to create: {len(repair_plan['links_to_create'])}")
        
        # Show sample links
        for i, link in enumerate(repair_plan['links_to_create'][:3], 1):
            att = link['attachment']
            msg = link['message']
            self.stdout.write(f"\n   {i}. Link attachment '{att.name}' to message:")
            self.stdout.write(f"      • Message ID: {msg.id}")
            self.stdout.write(f"      • Subject: '{msg.subject[:50]}...'")
            self.stdout.write(f"      • Date: {msg.created_at}")
            self.stdout.write(f"      • Reasoning: {link['reasoning']}")
        
        if len(repair_plan['links_to_create']) > 3:
            remaining = len(repair_plan['links_to_create']) - 3
            self.stdout.write(f"   • ... and {remaining} more links")

    def execute_repairs(self, repair_plan: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the repair plan."""
        
        successful_links = 0
        failed_links = 0
        errors = []
        
        with transaction.atomic():
            for link in repair_plan['links_to_create']:
                try:
                    attachment = link['attachment']
                    message = link['message']
                    
                    # Create the M2M link
                    attachment.messages.add(message)
                    
                    self.stdout.write(f"✅ Linked '{attachment.name}' to message {message.id}")
                    successful_links += 1
                    
                except Exception as e:
                    error_msg = f"Failed to link {attachment.name}: {e}"
                    self.stdout.write(self.style.ERROR(f"❌ {error_msg}"))
                    errors.append(error_msg)
                    failed_links += 1
        
        return {
            'successful_links': successful_links,
            'failed_links': failed_links,
            'errors': errors
        }

    def print_results(self, results: Dict[str, Any]):
        """Print the execution results."""
        
        self.stdout.write(f"\n📊 REPAIR RESULTS:")
        self.stdout.write(f"   • Successful links created: {results['successful_links']}")
        self.stdout.write(f"   • Failed links: {results['failed_links']}")
        
        if results['errors']:
            self.stdout.write(f"\n❌ ERRORS:")
            for error in results['errors']:
                self.stdout.write(f"   • {error}")
        
        if results['successful_links'] > 0:
            self.stdout.write(self.style.SUCCESS(f"\n🎉 Successfully created {results['successful_links']} attachment links!"))
            self.stdout.write("💡 You should now be able to see attachments in your email search results.")
