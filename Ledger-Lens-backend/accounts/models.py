from django.db import models
from django.utils import timezone
from django.contrib.auth.hashers import make_password, check_password
from datetime import timedelta
import json

# Create your models here.

class PDFUpload(models.Model):
    """Model to store uploaded PDF files and their extraction results"""
    file = models.FileField(upload_to='pdfs/')
    uploaded_at = models.DateTimeField(default=timezone.now)
    processed = models.BooleanField(default=False)
    processing_error = models.TextField(blank=True, null=True)
    
    # Extracted data fields
    account_info = models.JSONField(default=dict, blank=True)
    total_transactions = models.IntegerField(default=0)
    pages_processed = models.IntegerField(default=0)
    monthly_analysis = models.JSONField(default=dict, blank=True)
    
    # Progress tracking for resume
    extracted_text_pages = models.JSONField(default=list, blank=True)  # Store text per page
    current_page = models.IntegerField(default=0)  # Last completed page (0-indexed)
    
    def __str__(self):
        return f"PDF Upload {self.id} - {self.file.name}"
    
    class Meta:
        ordering = ['-uploaded_at']

class Transaction(models.Model):
    """Model to store individual transactions"""
    pdf_upload = models.ForeignKey(PDFUpload, on_delete=models.CASCADE, related_name='transactions')
    date = models.CharField(max_length=20)
    description = models.TextField()
    debit = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    
    def __str__(self):
        return f"{self.date} - {self.description[:50]}"
    
    class Meta:
        ordering = ['date']


class PasscodeConfig(models.Model):
    """Single-row model for passcode configuration and rate limiting"""
    passcode_hash = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    last_reset_at = models.DateTimeField(auto_now=True)
    
    # Rate limiting for passcode attempts
    passcode_attempts = models.IntegerField(default=0)
    passcode_locked_until = models.DateTimeField(null=True, blank=True)
    
    # Rate limiting for username/password attempts
    creds_attempts = models.IntegerField(default=0)
    creds_locked_until = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        verbose_name = "Passcode Configuration"
        verbose_name_plural = "Passcode Configuration"
    
    def __str__(self):
        return "Passcode Configuration"
    
    @classmethod
    def get_config(cls):
        """Get or create the single configuration instance"""
        config, created = cls.objects.get_or_create(
            pk=1,
            defaults={
                'passcode_hash': make_password('000000'),  # Default placeholder
                'expires_at': timezone.now() + timedelta(days=7)
            }
        )
        return config
    
    def is_passcode_valid(self, code: str) -> bool:
        """Check if provided passcode matches"""
        return check_password(code, self.passcode_hash)
    
    def is_passcode_expired(self) -> bool:
        """Check if passcode has expired"""
        return timezone.now() > self.expires_at
    
    def is_passcode_locked(self) -> bool:
        """Check if passcode attempts are locked"""
        if self.passcode_locked_until:
            if timezone.now() < self.passcode_locked_until:
                return True
            else:
                # Lock expired, reset
                self.passcode_attempts = 0
                self.passcode_locked_until = None
                self.save()
        return False
    
    def is_creds_locked(self) -> bool:
        """Check if credential attempts are locked"""
        if self.creds_locked_until:
            if timezone.now() < self.creds_locked_until:
                return True
            else:
                # Lock expired, reset
                self.creds_attempts = 0
                self.creds_locked_until = None
                self.save()
        return False
    
    def increment_passcode_attempts(self):
        """Increment passcode attempts and lock if needed"""
        self.passcode_attempts += 1
        if self.passcode_attempts >= 5:
            from datetime import timedelta
            self.passcode_locked_until = timezone.now() + timedelta(minutes=15)
        self.save()
    
    def increment_creds_attempts(self):
        """Increment credential attempts and lock if needed"""
        self.creds_attempts += 1
        if self.creds_attempts >= 5:
            from datetime import timedelta
            self.creds_locked_until = timezone.now() + timedelta(minutes=30)
        self.save()
    
    def reset_attempts(self):
        """Reset all attempt counters"""
        self.passcode_attempts = 0
        self.creds_attempts = 0
        self.passcode_locked_until = None
        self.creds_locked_until = None
        self.save()
    
    def reset_passcode(self, new_code: str):
        """Reset passcode and update expiration"""
        self.passcode_hash = make_password(new_code)
        self.expires_at = timezone.now() + timedelta(days=7)
        self.reset_attempts()
        self.save()
