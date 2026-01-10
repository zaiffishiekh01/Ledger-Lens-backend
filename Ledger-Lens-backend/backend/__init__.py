import logging
import threading

logger = logging.getLogger(__name__)

def resume_incomplete_pdfs():
    """Resume processing for incomplete PDFs on startup"""
    from accounts.models import PDFUpload
    from accounts.views import process_pdf_background
    
    try:
        incomplete_pdfs = PDFUpload.objects.filter(processed=False, processing_error__isnull=True)
        count = incomplete_pdfs.count()
        
        if count > 0:
            logger.info(f"[STARTUP] Found {count} incomplete PDF(s), resuming processing...")
            for pdf_upload in incomplete_pdfs:
                # Check if PDF file still exists
                if pdf_upload.file and pdf_upload.file.storage.exists(pdf_upload.file.name):
                    logger.info(f"[STARTUP] Resuming PDF {pdf_upload.id}")
                    thread = threading.Thread(target=process_pdf_background, args=(pdf_upload.id,), daemon=True)
                    thread.start()
                else:
                    logger.warning(f"[STARTUP] PDF {pdf_upload.id} file not found, marking as error")
                    pdf_upload.processing_error = "PDF file not found on restart"
                    pdf_upload.save()
        else:
            logger.info("[STARTUP] No incomplete PDFs found")
    except Exception as e:
        logger.error(f"[STARTUP] Error resuming incomplete PDFs: {str(e)}", exc_info=True)

# Resume incomplete PDFs when Django is ready (called from wsgi.py)
def startup_resume():
    """Called after Django is fully loaded"""
    try:
        resume_incomplete_pdfs()
    except Exception as e:
        logger.error(f"[STARTUP] Failed to resume incomplete PDFs: {str(e)}")
