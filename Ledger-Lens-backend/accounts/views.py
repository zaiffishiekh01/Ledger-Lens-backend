from django.shortcuts import render
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.db import transaction
import os
import json
import threading
import gc
import logging
from datetime import datetime
from .models import PDFUpload, Transaction, PasscodeConfig
from .pdf_extractor import BankStatementExtractor
from functools import wraps
from django.utils import timezone
from datetime import timedelta
from django.contrib.auth.hashers import check_password

# Configure logging
logger = logging.getLogger(__name__)

# Authentication decorator
def require_authentication(view_func):
    """Decorator to require authentication for views"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get('authenticated', False):
            return Response(
                {'error': 'Authentication required'}, 
                status=status.HTTP_401_UNAUTHORIZED
            )
        return view_func(request, *args, **kwargs)
    return wrapper

# Helper to extract only the required fields for the frontend
FRONTEND_FIELDS = [
    'customer_name',
    'account_number',
    'iban_number',
    'financial_period',
    'opening_balance',
    'closing_balance',
    'pages_processed',
    'total_transactions',
]

def get_frontend_result(pdf_upload):
    account_info = pdf_upload.account_info or {}
    return {
        'customer_name': account_info.get('customer_name', ''),
        'account_number': account_info.get('account_number', ''),
        'iban_number': account_info.get('iban_number', ''),
        'financial_period': account_info.get('financial_period', ''),
        'opening_balance': account_info.get('opening_balance', 0),
        'closing_balance': account_info.get('closing_balance', 0),
        'pages_processed': pdf_upload.pages_processed,
        'total_transactions': pdf_upload.total_transactions,
    }

def process_pdf_background(pdf_upload_id):
    """Process PDF in background thread"""
    start_time = datetime.now()
    extractor = None
    results = None
    temp_file_path = None
    try:
        logger.info(f"[PDF {pdf_upload_id}] Background processing started at {start_time}")
        
        # Immediate check: Verify PDF still exists
        try:
            pdf_upload = PDFUpload.objects.get(id=pdf_upload_id)
        except PDFUpload.DoesNotExist:
            logger.info(f"[PDF {pdf_upload_id}] PDF deleted before processing started, stopping")
            return
        
        # Check if already processed (thread safety)
        if pdf_upload.processed:
            logger.info(f"[PDF {pdf_upload_id}] Already processed, skipping")
            return
        
        # Get file path (works with both local and Supabase Storage)
        # Check storage type - S3 storage doesn't support .path attribute
        from django.core.files.storage import default_storage
        is_s3_storage = hasattr(default_storage, 'bucket_name') or 's3' in str(type(default_storage)).lower()
        
        if is_s3_storage:
            # Supabase Storage (S3) - download to temp file
            import tempfile
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
            temp_file.write(pdf_upload.file.read())
            temp_file.close()
            file_path = temp_file.name
            temp_file_path = file_path  # Store for cleanup
            logger.info(f"[PDF {pdf_upload_id}] Downloaded from Supabase Storage to temp file: {file_path}")
        else:
            # Local filesystem
            file_path = pdf_upload.file.path
        
        logger.info(f"[PDF {pdf_upload_id}] File path: {file_path}")
        
        # Check for existing progress (resume from last page)
        start_page = 0
        existing_text = []
        if pdf_upload.extracted_text_pages and len(pdf_upload.extracted_text_pages) > 0:
            start_page = pdf_upload.current_page + 1
            existing_text = pdf_upload.extracted_text_pages.copy()
            logger.info(f"[PDF {pdf_upload_id}] Resuming from page {start_page + 1}, {len(existing_text)} pages already extracted")
        else:
            logger.info(f"[PDF {pdf_upload_id}] Starting PDF extraction from beginning...")
        
        # Check before extraction: Verify PDF still exists
        try:
            PDFUpload.objects.get(id=pdf_upload_id)
        except PDFUpload.DoesNotExist:
            logger.info(f"[PDF {pdf_upload_id}] PDF deleted before extraction started, stopping")
            return
        
        extractor = BankStatementExtractor()
        logger.info(f"[PDF {pdf_upload_id}] Extractor initialized, calling process_bank_statement...")
        
        # Pass pdf_upload_id, start_page, and existing_text for resume
        results = extractor.process_bank_statement(file_path, pdf_upload_id, start_page, existing_text)
        
        # Check if processing was stopped (PDF deleted)
        if results and 'error' in results and results['error'] == "PDF processing stopped":
            logger.info(f"[PDF {pdf_upload_id}] Processing stopped - PDF deleted during extraction")
            return
        
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"[PDF {pdf_upload_id}] PDF extraction completed in {elapsed:.2f} seconds")
        
        if not results or 'error' in results:
            logger.error(f"[PDF {pdf_upload_id}] Extraction error: {results['error']}")
            pdf_upload.processing_error = results['error']
            pdf_upload.save()
            return
        
        # Log extraction results
        pages_processed = results.get('pages_processed', 0)
        total_transactions = results.get('total_transactions', 0)
        logger.info(f"[PDF {pdf_upload_id}] Extraction results: {pages_processed} pages, {total_transactions} transactions")
        
        # Calculate analytics if available
        logger.info(f"[PDF {pdf_upload_id}] Calculating analytics...")
        analytics = results.get('analytics', {})
        if not analytics and results.get('monthly_analysis'):
            analytics = extractor.calculate_analytics(results.get('monthly_analysis', {}))
        logger.info(f"[PDF {pdf_upload_id}] Analytics calculated")
        
        logger.info(f"[PDF {pdf_upload_id}] Saving results to database...")
        pdf_upload.processed = True
        pdf_upload.account_info = results.get('account_info', {})
        pdf_upload.total_transactions = total_transactions
        pdf_upload.pages_processed = pages_processed
        pdf_upload.monthly_analysis = results.get('monthly_analysis', {})
        # Store analytics in account_info for easy access
        if analytics:
            pdf_upload.account_info['analytics'] = analytics
        # Clear progress tracking fields on completion
        pdf_upload.extracted_text_pages = []
        pdf_upload.current_page = 0
        pdf_upload.save()
        logger.info(f"[PDF {pdf_upload_id}] Results saved to database")
        
        # Create transaction records
        transactions = results.get('transactions', [])
        logger.info(f"[PDF {pdf_upload_id}] Creating {len(transactions)} transaction records...")
        transaction_count = 0
        for transaction_data in transactions:
            Transaction.objects.create(
                pdf_upload=pdf_upload,
                date=transaction_data.get('date', ''),
                description=transaction_data.get('description', ''),
                debit=transaction_data.get('debit', 0),
                credit=transaction_data.get('credit', 0),
                balance=transaction_data.get('balance', 0)
            )
            transaction_count += 1
            if transaction_count % 100 == 0:
                logger.info(f"[PDF {pdf_upload_id}] Created {transaction_count}/{len(transactions)} transactions...")
        
        logger.info(f"[PDF {pdf_upload_id}] All {transaction_count} transactions created")
        
        total_elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"[PDF {pdf_upload_id}] Processing completed successfully in {total_elapsed:.2f} seconds")
        
        # Memory cleanup: explicitly delete large objects
        del results
        del analytics
        if extractor:
            del extractor
        
        # Force garbage collection to free memory
        gc.collect()
        logger.info(f"[PDF {pdf_upload_id}] Memory cleaned up")
        
        # Cleanup temp file if used (Supabase Storage)
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
                logger.info(f"[PDF {pdf_upload_id}] Temp file deleted: {temp_file_path}")
            except Exception as cleanup_error:
                logger.warning(f"[PDF {pdf_upload_id}] Failed to delete temp file: {cleanup_error}")
    except Exception as e:
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.error(f"[PDF {pdf_upload_id}] ERROR after {elapsed:.2f} seconds: {str(e)}", exc_info=True)
        try:
            pdf_upload = PDFUpload.objects.get(id=pdf_upload_id)
            pdf_upload.processing_error = str(e)
            pdf_upload.save()
            logger.error(f"[PDF {pdf_upload_id}] Error saved to database")
        except Exception as save_error:
            logger.error(f"[PDF {pdf_upload_id}] Failed to save error: {str(save_error)}")
        finally:
            # Cleanup in case of error
            if results:
                del results
            if extractor:
                del extractor
            # Cleanup temp file if used (Supabase Storage)
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                    logger.info(f"[PDF {pdf_upload_id}] Temp file deleted after error: {temp_file_path}")
                except Exception as cleanup_error:
                    logger.warning(f"[PDF {pdf_upload_id}] Failed to delete temp file after error: {cleanup_error}")
            gc.collect()
            logger.info(f"[PDF {pdf_upload_id}] Cleanup completed after error")

@api_view(['POST'])
@parser_classes([MultiPartParser, FormParser])
@require_authentication
def upload_pdf(request):
    """Upload PDF and return immediately, process in background"""
    try:
        if 'file' not in request.FILES:
            logger.warning("Upload request with no file provided")
            return Response(
                {'error': 'No file provided'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        pdf_file = request.FILES['file']
        file_size = pdf_file.size
        logger.info(f"Upload request received: {pdf_file.name}, size: {file_size} bytes")
        
        if not pdf_file.name.lower().endswith('.pdf'):
            logger.warning(f"Invalid file type: {pdf_file.name}")
            return Response(
                {'error': 'Only PDF files are allowed'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Clear all pending PDFs and their data before uploading new one
        logger.info("Clearing all pending PDF uploads and storage...")
        pending_pdfs = PDFUpload.objects.filter(processed=False)
        pending_count = pending_pdfs.count()
        logger.info(f"Found {pending_count} pending PDF(s) to delete")
        
        with transaction.atomic():
            for pdf_upload in pending_pdfs:
                # Delete file from storage (S3 or local)
                if pdf_upload.file:
                    try:
                        pdf_upload.file.delete(save=False)
                        logger.info(f"[PDF {pdf_upload.id}] File deleted from storage")
                    except Exception as e:
                        logger.warning(f"[PDF {pdf_upload.id}] Error deleting file: {str(e)}")
                # Delete the PDF upload record (transactions deleted via CASCADE)
                pdf_upload.delete()
                logger.info(f"[PDF {pdf_upload.id}] PDF upload record deleted")
        
        logger.info(f"Cleared {pending_count} pending PDF(s) and their data")
        
        # Create PDF upload record
        logger.info(f"Creating PDF upload record for: {pdf_file.name}")
        pdf_upload = PDFUpload.objects.create(
            file=pdf_file,
            processed=False
        )
        logger.info(f"[PDF {pdf_upload.id}] Upload record created successfully")
        
        # Thread safety: Check if already processed before starting thread
        # (This prevents duplicate processing if upload endpoint is called multiple times)
        if not pdf_upload.processed:
            logger.info(f"[PDF {pdf_upload.id}] Starting background processing thread...")
            # Start background processing
            thread = threading.Thread(target=process_pdf_background, args=(pdf_upload.id,))
            thread.daemon = True
            thread.start()
            logger.info(f"[PDF {pdf_upload.id}] Background thread started, thread ID: {thread.ident}")
        else:
            logger.warning(f"[PDF {pdf_upload.id}] Already processed, skipping thread start")
        
        # Return immediately with PDF ID for polling
        logger.info(f"[PDF {pdf_upload.id}] Returning 202 Accepted response to client")
        return Response(
            {
                'id': pdf_upload.id,
                'status': 'processing',
                'message': 'PDF uploaded successfully. Processing in background.'
            },
            status=status.HTTP_202_ACCEPTED
        )
    except Exception as e:
        logger.error(f"Error in upload_pdf: {str(e)}", exc_info=True)
        return Response(
            {'error': f'Error uploading file: {str(e)}'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(['GET'])
@require_authentication
def get_pdf_results(request, pdf_id):
    """Get PDF processing results - returns full data when processed, status when processing"""
    try:
        logger.info(f"[PDF {pdf_id}] Polling request received from client")
        pdf_upload = PDFUpload.objects.get(id=pdf_id)
        
        # Calculate time since upload
        time_since_upload = None
        if pdf_upload.uploaded_at:
            time_since_upload = (datetime.now() - pdf_upload.uploaded_at.replace(tzinfo=None)).total_seconds()
        
        # If already processed, return results immediately (thread safety)
        if pdf_upload.processed:
            logger.info(f"[PDF {pdf_id}] Status: COMPLETED (processed: {pdf_upload.pages_processed} pages, {pdf_upload.total_transactions} transactions, elapsed: {time_since_upload:.2f}s)")
            account_info = pdf_upload.account_info or {}
            account_info['pages_processed'] = pdf_upload.pages_processed
            account_info['total_transactions'] = pdf_upload.total_transactions
            analytics = account_info.pop('analytics', {})
            response_data = {
                'id': pdf_upload.id,
                'status': 'completed',
                'account_info': account_info,
                'monthly_analysis': pdf_upload.monthly_analysis or {},
                'analytics': analytics
            }
            return Response(response_data, status=status.HTTP_200_OK)
        
        # If still processing, return status
        if pdf_upload.processing_error:
            logger.warning(f"[PDF {pdf_id}] Status: ERROR - {pdf_upload.processing_error} (elapsed: {time_since_upload:.2f}s)")
            return Response(
                {
                    'id': pdf_upload.id,
                    'status': 'error',
                    'error': pdf_upload.processing_error
                },
                status=status.HTTP_200_OK
            )
        
        # Still processing
        logger.info(f"[PDF {pdf_id}] Status: PROCESSING (elapsed: {time_since_upload:.2f}s, processed: {pdf_upload.processed})")
        return Response(
            {
                'id': pdf_upload.id,
                'status': 'processing',
                'message': 'PDF is being processed. Please check again in a moment.'
            },
            status=status.HTTP_200_OK
        )
    except PDFUpload.DoesNotExist:
        logger.warning(f"[PDF {pdf_id}] Polling request for non-existent PDF")
        return Response(
            {'error': 'PDF upload not found'}, 
            status=status.HTTP_404_NOT_FOUND
        )
    except Exception as e:
        logger.error(f"[PDF {pdf_id}] Error in get_pdf_results: {str(e)}", exc_info=True)
        return Response(
            {'error': f'Error retrieving results: {str(e)}'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(['GET'])
@require_authentication
def list_pdf_uploads(request):
    try:
        pdf_uploads = PDFUpload.objects.all()
        response_data = [get_frontend_result(pdf) for pdf in pdf_uploads]
        return Response(response_data, status=status.HTTP_200_OK)
    except Exception as e:
        return Response(
            {'error': f'Error listing PDF uploads: {str(e)}'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(['POST'])
@require_authentication
def stop_pdf_processing(request, pdf_id):
    """Stop processing and delete PDF when frontend times out"""
    try:
        logger.info(f"[PDF {pdf_id}] Stop processing request received")
        pdf_upload = PDFUpload.objects.get(id=pdf_id)
        
        if pdf_upload.file:
            pdf_upload.file.delete(save=False)
            logger.info(f"[PDF {pdf_id}] PDF file deleted")
        
        pdf_upload.delete()
        logger.info(f"[PDF {pdf_id}] PDF upload record deleted")
        
        return Response(
            {'message': 'PDF processing stopped and deleted successfully'}, 
            status=status.HTTP_200_OK
        )
    except PDFUpload.DoesNotExist:
        logger.warning(f"[PDF {pdf_id}] Stop request for non-existent PDF")
        return Response(
            {'error': 'PDF upload not found'}, 
            status=status.HTTP_404_NOT_FOUND
        )
    except Exception as e:
        logger.error(f"[PDF {pdf_id}] Error stopping processing: {str(e)}", exc_info=True)
        return Response(
            {'error': f'Error stopping processing: {str(e)}'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(['DELETE'])
@require_authentication
def delete_pdf_upload(request, pdf_id):
    try:
        pdf_upload = PDFUpload.objects.get(id=pdf_id)
        if pdf_upload.file:
            pdf_upload.file.delete(save=False)
        pdf_upload.delete()
        return Response(
            {'message': 'PDF upload deleted successfully'}, 
            status=status.HTTP_204_NO_CONTENT
        )
    except PDFUpload.DoesNotExist:
        return Response(
            {'error': 'PDF upload not found'}, 
            status=status.HTTP_404_NOT_FOUND
        )
    except Exception as e:
        return Response(
            {'error': f'Error deleting PDF upload: {str(e)}'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# Authentication views
@api_view(['POST'])
def login_with_passcode(request):
    """Login with 6-digit passcode"""
    try:
        passcode = request.data.get('passcode', '').strip()
        
        if not passcode or len(passcode) != 6 or not passcode.isdigit():
            return Response(
                {'error': 'Passcode must be exactly 6 digits'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        config = PasscodeConfig.get_config()
        
        # Check if locked
        if config.is_passcode_locked():
            remaining = (config.passcode_locked_until - timezone.now()).total_seconds()
            minutes = int(remaining / 60) + 1
            return Response(
                {'error': f'Too many failed attempts. Please wait {minutes} minutes.'}, 
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )
        
        # Check if expired
        if config.is_passcode_expired():
            return Response(
                {'error': 'Passcode has expired. Please reset using admin credentials.'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Validate passcode
        if config.is_passcode_valid(passcode):
            request.session['authenticated'] = True
            config.reset_attempts()
            return Response({'success': True, 'message': 'Login successful'})
        else:
            config.increment_passcode_attempts()
            remaining_attempts = 5 - config.passcode_attempts
            if remaining_attempts > 0:
                return Response(
                    {'error': f'Incorrect passcode. {remaining_attempts} attempts remaining.'}, 
                    status=status.HTTP_401_UNAUTHORIZED
                )
            else:
                return Response(
                    {'error': 'Too many failed attempts. Please wait 15 minutes.'}, 
                    status=status.HTTP_429_TOO_MANY_REQUESTS
                )
    except Exception as e:
        return Response(
            {'error': f'Login error: {str(e)}'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
def reset_passcode(request):
    """Reset passcode using admin credentials"""
    try:
        username = request.data.get('username', '').strip()
        password = request.data.get('password', '').strip()
        new_passcode = request.data.get('new_passcode', '').strip()
        confirm_passcode = request.data.get('confirm_passcode', '').strip()
        
        # Validate inputs
        if not username or not password:
            return Response(
                {'error': 'Username and password are required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not new_passcode or len(new_passcode) != 6 or not new_passcode.isdigit():
            return Response(
                {'error': 'New passcode must be exactly 6 digits'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if new_passcode != confirm_passcode:
            return Response(
                {'error': 'New passcodes do not match'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        config = PasscodeConfig.get_config()
        
        # Check if locked
        if config.is_creds_locked():
            remaining = (config.creds_locked_until - timezone.now()).total_seconds()
            minutes = int(remaining / 60) + 1
            return Response(
                {'error': f'Too many failed attempts. Please wait {minutes} minutes.'}, 
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )
        
        # Verify credentials
        admin_username = os.getenv('ADMIN_USERNAME', 'admin')
        admin_password = os.getenv('ADMIN_PASSWORD', '')
        
        if username != admin_username or password != admin_password:
            config.increment_creds_attempts()
            remaining_attempts = 5 - config.creds_attempts
            if remaining_attempts > 0:
                return Response(
                    {'error': f'Invalid username or password. {remaining_attempts} attempts remaining.'}, 
                    status=status.HTTP_401_UNAUTHORIZED
                )
            else:
                return Response(
                    {'error': 'Too many failed attempts. Please wait 30 minutes.'}, 
                    status=status.HTTP_429_TOO_MANY_REQUESTS
                )
        
        # Reset passcode
        config.reset_passcode(new_passcode)
        return Response({'success': True, 'message': 'Passcode reset successful'})
        
    except Exception as e:
        return Response(
            {'error': f'Reset error: {str(e)}'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
def check_auth_status(request):
    """Check authentication status, passcode setup status, and lockout status"""
    authenticated = request.session.get('authenticated', False)
    config = PasscodeConfig.get_config()
    # Check if passcode is still default (not set up)
    is_default = check_password('000000', config.passcode_hash)
    
    # Check passcode lockout status
    passcode_locked = False
    passcode_lockout_minutes = None
    if config.is_passcode_locked():
        remaining = (config.passcode_locked_until - timezone.now()).total_seconds()
        passcode_locked = True
        passcode_lockout_minutes = max(1, int(remaining / 60))
    
    # Check credentials lockout status
    creds_locked = False
    creds_lockout_minutes = None
    if config.is_creds_locked():
        remaining = (config.creds_locked_until - timezone.now()).total_seconds()
        creds_locked = True
        creds_lockout_minutes = max(1, int(remaining / 60))
    
    # Never cache auth status so returning users get fresh passcode_setup_required
    # (otherwise browser/proxy could serve stale "setup required" after user has set passcode)
    return Response(
        {
            'authenticated': authenticated,
            'passcode_setup_required': is_default,
            'passcode_locked': passcode_locked,
            'passcode_lockout_minutes': passcode_lockout_minutes,
            'creds_locked': creds_locked,
            'creds_lockout_minutes': creds_lockout_minutes,
        },
        headers={
            'Cache-Control': 'no-store, no-cache, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0',
        },
    )


@api_view(['POST'])
def logout(request):
    """Logout and clear session"""
    request.session.flush()
    return Response({'success': True, 'message': 'Logged out successfully'})
