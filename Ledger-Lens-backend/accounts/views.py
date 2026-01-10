from django.shortcuts import render
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
import os
import json
import threading
import gc
import logging
from datetime import datetime
from .models import PDFUpload, Transaction
from .pdf_extractor import BankStatementExtractor

# Configure logging
logger = logging.getLogger(__name__)

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
    try:
        logger.info(f"[PDF {pdf_upload_id}] Background processing started at {start_time}")
        pdf_upload = PDFUpload.objects.get(id=pdf_upload_id)
        
        # Check if already processed (thread safety)
        if pdf_upload.processed:
            logger.info(f"[PDF {pdf_upload_id}] Already processed, skipping")
            return
        
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
            gc.collect()
            logger.info(f"[PDF {pdf_upload_id}] Cleanup completed after error")

@api_view(['POST'])
@parser_classes([MultiPartParser, FormParser])
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
