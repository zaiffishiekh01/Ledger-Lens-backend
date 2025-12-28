import os
import cv2
import numpy as np
from datetime import datetime
from collections import defaultdict
import fitz  # PyMuPDF
import pytesseract
import json
import re

class BankStatementExtractor:
    def __init__(self, config_path=None):
        # English patterns
        self.english_patterns = {
            'customer_name': ['Customer Name', 'Account Holder'],
            'city': ['City'],
            'account_number': ['Account Number'],
            'iban_number': ['IBAN Number', 'IBAN'],
            'opening_balance': ['Opening Balance'],
            'closing_balance': ['Closing Balance'],
            'financial_period': ['On The Period', 'Period'],
            'date': ['Date'],
            'debit': ['Debit'],
            'credit': ['Credit'],
            'balance': ['Balance']
        }
        
        # Arabic text patterns for field extraction
        self.arabic_patterns = {
            'customer_name': ['اسم العميل', 'اسم المعميل'],
            'city': ['المدينة', 'مدينة'],
            'account_number': ['رقم الحساب', 'رقم حساب'],
            'iban_number': ['رقم الآيبان', 'رقم آيبان', 'IBAN'],
            'opening_balance': ['رصيد الحساب الافتتاحي', 'الرصيد الافتتاحي'],
            'closing_balance': ['رصيد الإقفال', 'الرصيد الإقفالي'],
            'financial_period': ['خلال الفترة', 'الفترة المالية'],
            'date': ['التاريخ', 'تاريخ'],
            'debit': ['مدين', 'خصم'],
            'credit': ['دائن', 'ايداع'],
            'balance': ['الرصيد', 'رصيد']
        }
        """
        Initialize extractor with JSON configuration
        
        Args:
            config_path (str): Path to JSON configuration file. If None, uses default config.
        """
        if config_path and os.path.exists(config_path):
            self.load_config_from_file(config_path)
        else:
            self.load_default_config()

    
    
    def load_config_from_file(self, config_path):
        """Load configuration from JSON file"""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
            print(f"Configuration loaded from: {config_path}")
        except Exception as e:
            print(f"Error loading config file: {e}")
            print("Using default configuration...")
            self.load_default_config()
    
    def load_default_config(self):
        """Load default configuration"""
        self.config = {
            "ocr_settings": {
                "combined_config": "--oem 3 --psm 6 -l ara+eng"
            },
            "field_patterns": {
                "arabic": {
                    "customer_name": {
                        "keywords": ["اسم العميل", "اسم المعميل"],
                        "separators": [":", "=", "-"]
                    },
                    "account_number": {
                        "keywords": ["رقم الحساب", "رقم حساب"],
                        "separators": [":", "=", "-"]
                    },
                    "opening_balance": {
                        "keywords": ["رصيد الحساب الافتتاحي", "الرصيد الافتتاحي"],
                        "separators": [":", "=", "-"],
                        "data_type": "amount"
                    },
                    "closing_balance": {
                        "keywords": ["رصيد الإقفال", "الرصيد الإقفالي"],
                        "separators": [":", "=", "-"],
                        "data_type": "amount"
                    }
                },
                "english": {
                    "customer_name": {
                        "keywords": ["Customer Name", "Account Holder"],
                        "separators": [":", "=", "-"]
                    },
                    "account_number": {
                        "keywords": ["Account Number"],
                        "separators": [":", "=", "-"]
                    },
                    "opening_balance": {
                        "keywords": ["Opening Balance"],
                        "separators": [":", "=", "-"],
                        "data_type": "amount"
                    },
                    "closing_balance": {
                        "keywords": ["Closing Balance"],
                        "separators": [":", "=", "-"],
                        "data_type": "amount"
                    }
                }
            },
            "date_patterns": [
                {
                    "separators": ["/", "-"],
                    "year_position": 0,
                    "month_position": 1,
                    "day_position": 2
                },
                {
                    "separators": ["/", "-"],
                    "year_position": 2,
                    "month_position": 1,
                    "day_position": 0
                }
            ],
            "amount_patterns": {
                "currency_symbols": ["SAR", "SR", "ريال", "ر.س"],
                "clean_chars": ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", ".", ",", "-"]
            },
            "transaction_table": {
                "search_lines_after": 10
            }
        }
    
    def save_config_template(self, output_path):
        """Save current configuration as a template JSON file"""
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)
        print(f"Configuration template saved to: {output_path}")
    
    def get_ocr_config(self, config_type="combined_config"):
        """Get OCR configuration"""
        return self.config["ocr_settings"].get(config_type, "--oem 3 --psm 6")

    def extract_single_amount_rtl(self,line):
        """Extract a single monetary amount from a line"""
        # Pattern to match amounts like "25,631.50 SAR" or "0.00 SAR"
        amount_pattern = r'(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*SAR'
        
        match = re.search(amount_pattern, line)
        if match:
            # Clean the amount (remove commas)
            clean_amount = match.group(1).replace(',', '')
            try:
                return float(clean_amount)
            except ValueError:
                pass
        
        return None


    def extract_date_from_line_rtl(self,line):
        """Extract date from line in format YYYY/MM/DD"""
        # Pattern for date like "2024/06/17"
        date_pattern = r'(\d{4}/\d{2}/\d{2})'
        
        match = re.search(date_pattern, line)
        if match:
            return match.group(1)
        
        return None


    def extract_field_value_different(self, text, field_name, language):
        """Extract field value from text based on patterns - using second occurrence"""
        patterns = self.arabic_patterns if language == 'arabic' else self.english_patterns
        
        if field_name not in patterns:
            return None
        
        for pattern in patterns[field_name]:
            # Create regex pattern to find ALL occurrences
            if language == 'arabic':
                regex = rf'{re.escape(pattern)}\s*[:\s]*([^\n\r]+)'
            else:
                regex = rf'{re.escape(pattern)}\s*[:\s]*([^\n\r]+)'
            
            matches = re.findall(regex, text, re.MULTILINE | re.IGNORECASE)
            if len(matches) >= 2:
                # Use the second occurrence
                value = matches[1].strip()
                # Clean up the value
                value = re.sub(r'\s+', ' ', value)
                return value
            elif len(matches) == 1:
                # Fallback to first if only one exists
                value = matches[0].strip()
                value = re.sub(r'\s+', ' ', value)
                return value
        
        return None

       


    def extract_field_value(self, text, field_name, language):
        """Extract field value from text based on patterns"""
        patterns = self.arabic_patterns if language == 'arabic' else self.english_patterns
        
        if field_name not in patterns:
            return None
        
        for pattern in patterns[field_name]:
            # Create regex pattern to find the field and its value
            if language == 'arabic':
                # For Arabic, look for pattern followed by value
                regex = rf'{re.escape(pattern)}\s*[:\s]*([^\n\r]+)'
            else:
                # For English, similar approach
                regex = rf'{re.escape(pattern)}\s*[:\s]*([^\n\r]+)'
            
            match = re.search(regex, text, re.MULTILINE | re.IGNORECASE)
            if match:
                value = match.group(1).strip()
                # Clean up the value
                value = re.sub(r'\s+', ' ', value)
                return value
        
        return None


    
    
    
    def extract_date_from_text(self, text):
        """Extract date from text using JSON patterns"""
        date_patterns = self.config.get("date_patterns", [])
        lines = text.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            for pattern_config in date_patterns:
                separators = pattern_config.get("separators", ["/", "-"])
                
                for separator in separators:
                    if separator in line:
                        parts = line.split(separator)
                        if len(parts) >= 3:
                            # Try to extract date components
                            try:
                                year_pos = pattern_config.get("year_position", 0)
                                month_pos = pattern_config.get("month_position", 1)
                                day_pos = pattern_config.get("day_position", 2)
                                
                                year = int(parts[year_pos])
                                month = int(parts[month_pos])
                                day = int(parts[day_pos])
                                
                                # Basic validation
                                if 1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
                                    return (year, month, day)
                            except (ValueError, IndexError):
                                continue
        
        return None
    
    def clean_amount(self, amount_str):
        """Clean and extract numeric amount from string"""
        if not amount_str:
            return None
        
        amount_config = self.config.get("amount_patterns", {})
        clean_chars = amount_config.get("clean_chars", ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", ".", ",", "-"])
        
        # Clean the string
        cleaned = ""
        for char in amount_str:
            if char in clean_chars:
                cleaned += char
        
        if not cleaned:
            return None
        
        # Handle comma-separated numbers
        if "," in cleaned:
            cleaned = cleaned.replace(",", "")
        
        try:
            return float(cleaned)
        except ValueError:
            return None
    
    def preprocess_image(self, image):
        """Preprocess image for better OCR accuracy"""
        # Convert to grayscale
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image
        
        # Apply Gaussian blur to reduce noise
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        
        # Apply adaptive threshold
        thresh = cv2.adaptiveThreshold(
            blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )
        
        # Morphological operations to clean up
        kernel = np.ones((2, 2), np.uint8)
        cleaned = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
        
        return cleaned

    def extract_text_from_pdf(self, pdf_path):
        """Extract text from PDF using PyMuPDF and OCR"""
        doc = fitz.open(pdf_path)
        all_text = []
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            
            # Try to extract text directly first
            direct_text = page.get_text()
            
            # Convert page to image for OCR
            mat = fitz.Matrix(2.0, 2.0)  # Higher resolution
            pix = page.get_pixmap(matrix=mat)
            img_data = pix.tobytes("png")
            
            # Convert to OpenCV format
            nparr = np.frombuffer(img_data, np.uint8)
            image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            # Preprocess image
            processed_image = self.preprocess_image(image)
            
            # OCR extraction with Arabic+English
            ocr_text = pytesseract.image_to_string(
                processed_image, config=self.get_ocr_config()
            )
            
            # Combine direct text and OCR text
            combined_text = direct_text + "\n" + ocr_text
            all_text.append(combined_text)
        
        doc.close()
        return all_text

    def extract_account_info(self, text, language, different_amount_format):
        """Extract basic account information"""
        info = {}
        
        # Extract each field
        fields = ['customer_name', 'city', 'account_number', 'iban_number', 
                 'opening_balance', 'closing_balance', 'financial_period']
        
        if different_amount_format:
            for field in fields:
                value = self.extract_field_value_different(text, field, language)
                info[field] = value
        else:
            for field in fields:
                value = self.extract_field_value(text, field, language)
                info[field] = value
        

        
        # Clean and format specific fields
        if info.get('opening_balance'):
            info['opening_balance'] = self.clean_amount(info['opening_balance'])
        
        if info.get('closing_balance'):
            info['closing_balance'] = self.clean_amount(info['closing_balance'])

        
        return info


    def date_to_datetime(self, date_str):
        """Convert date string or tuple to datetime object"""
        try:
            if isinstance(date_str, tuple):
                # Handle tuple format (year, month, day)
                year, month, day = date_str
                return datetime(int(year), int(month), int(day))
            else:
                # Handle string format
                if '/' in str(date_str):
                    parts = str(date_str).split('/')
                    if len(parts) == 3:
                        if len(parts[0]) == 4:  # YYYY/MM/DD
                            return datetime(int(parts[0]), int(parts[1]), int(parts[2]))
                        else:  # DD/MM/YYYY
                            return datetime(int(parts[2]), int(parts[1]), int(parts[0]))
                elif '-' in str(date_str):
                    parts = str(date_str).split('-')
                    if len(parts) == 3:
                        return datetime(int(parts[0]), int(parts[1]), int(parts[2]))
            return None
        except:
            return None

    def extract_header_from_second_page(self, text_pages):
        """Extract header text from the specific position in second page"""
        if len(text_pages) < 2:
            print("Not enough pages to extract header from second page")
            return None
        
        second_page_text = text_pages[1]  # Index 1 for second page
        lines = second_page_text.split('\n')
        

        if len(lines) >= 3:
            line_3 = lines[2].strip()  # Index 2 for line 3
            is_date_header = 'Date' in line_3 or 'ﺗﺎﺭﻳﺦ ﺍﻟﻌﻤﻠﻴﺔ'  in line_3

            different_amount_format = 'ﺗﺎﺭﻳﺦ ﺍﻟﻌﻤﻠﻴﺔ'  in line_3
            

        return {
            'is_LTR': is_date_header,
            'different_amount_format' : different_amount_format
                }
        
        # Look for table header patterns - specifically looking for the transaction table
        # This should be a line that contains multiple column headers like Date, Debit, Credit, Balance
        table_header_found = False
        
        for i, line in enumerate(lines):
            line_clean = line.strip()
            if not line_clean:
                continue
            
            # Look for lines that contain multiple header keywords (indicating it's a table header)
            header_keywords = ['Date', 'التاريخ', 'Debit', 'مدين', 'Credit', 'دائن', 'Balance', 'الرصيد', 'Transaction Details']
            keyword_count = sum(1 for keyword in header_keywords if keyword in line_clean)
            
            # A proper table header should contain at least 2-3 header keywords
            if keyword_count >= 2:
                table_header_found = True
                
                # Try different methods to split the header line
                headers = []
                
                # Method 1: Tab separation
                if '\t' in line_clean:
                    headers = [h.strip() for h in line_clean.split('\t') if h.strip()]
                # Method 2: Multiple spaces (2 or more consecutive spaces)
                elif re.search(r'\s{2,}', line_clean):
                    headers = [h.strip() for h in re.split(r'\s{2,}', line_clean) if h.strip()]
                # Method 3: Look for specific patterns with mixed separators
                else:
                    # Try to identify columns by looking for the pattern structure
                    # This handles cases where headers might be separated inconsistently
                    potential_headers = re.findall(r'[A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s]*(?=[A-Z\u0600-\u06FF]|$)', line_clean)
                    if potential_headers:
                        headers = [h.strip() for h in potential_headers if h.strip()]
                    else:
                        # Fallback: split by single space but try to group meaningful words
                        words = line_clean.split()
                        headers = []
                        current_header = ""
                        for word in words:
                            if word in header_keywords:
                                if current_header:
                                    headers.append(current_header.strip())
                                current_header = word
                            else:
                                current_header += " " + word
                        if current_header:
                            headers.append(current_header.strip())
                
                    
                    return {
                        'is_LTR': is_date_header,
                    }
        
        # If no proper table header found, show some context for debugging
        if not table_header_found:
            print(f"\n=== NO TABLE HEADER FOUND ===")
            print(f"Showing more context from second page (lines 10-20):")
            for i in range(9, min(20, len(lines))):
                if lines[i].strip():
                    print(f"  Line {i+1}: '{lines[i].strip()}'")
        
        return None

    def extract_transactions(self, text_pages, language, different_amount_format):
        """Extract transaction details from all pages"""
        transactions = []
        last_valid_date = None
        
        amount_config = self.config.get("amount_patterns", {})
        currency_symbols = amount_config.get("currency_symbols", ["SAR"])
        
        for page_idx, page_text in enumerate(text_pages): 
            lines = page_text.split('\n')
            
            for i, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue


                # Look for date patterns in the line
                date_match = self.extract_date_from_text(line)
                
                if date_match:
                    current_date = self.date_to_datetime(date_match)
                    
                    if not current_date:
                        continue
                    
                    # Date validation
                    if last_valid_date is not None and current_date < last_valid_date:
                        continue
                    
                    # Check for monetary amounts in current and next few lines
                    table_config = self.config.get("transaction_table", {})
                    search_lines_count = table_config.get("search_lines_after", 10)
                    search_lines = lines[i:i + search_lines_count]
                    full_text = ' '.join(search_lines)
                    
                    # Amount detection
                    has_amount = self.detect_amounts_in_text(full_text, currency_symbols)
                    
                    if has_amount:
                        transaction = self.parse_transaction_line(lines, i, language, different_amount_format)
                        # transaction = self.parse_transaction_line(lines, i, language)
                        if transaction:
                            # Check for duplicate transactions
                            if transactions:
                                last_transaction = transactions[-1]
                                if (last_transaction['date'] == transaction['date'] and 
                                    last_transaction['description'] == transaction['description'] and
                                    abs(float(last_transaction['balance']) - float(transaction['balance'])) < 0.01):
                                    continue
                            transactions.append(transaction)
                            last_valid_date = current_date
        
        return transactions




    def extract_rtl_transactions(self, text_pages, language):
        """Extract transaction details from all pages"""
        transactions = []
        last_valid_date = None
        
        amount_config = self.config.get("amount_patterns", {})
        currency_symbols = amount_config.get("currency_symbols", ["SAR"])
        
        for page_idx, page_text in enumerate(text_pages): 
            lines = page_text.split('\n')
            
            for i, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue


                # FIRST CONDITION: Look for first amount (Balance)
                first_amount = self.extract_single_amount_rtl(line)
                if first_amount is not None:
                    # Look for next 2 amounts in subsequent lines (within reasonable range)
                    amounts = [first_amount]
                    j = i + 1
                    
                    # Search for second and third amounts within next 15 lines
                    while j < len(lines) and j < i + 3 and len(amounts) < 3:
                        next_line = lines[j].strip()
                        if next_line:
                            amount = self.extract_single_amount_rtl(next_line)
                            if amount is not None:
                                amounts.append(amount)
                        j += 1
                    
                    # Check if we found exactly 3 amounts
                    if len(amounts) == 3:


                        # Look for date in the following lines (extended range since dates are far)
                        date_match = None
                        description_lines = []
                        
                        # Search for date and description in next 50 lines from where we started
                        for k in range(i, min(i + 50, len(lines))):
                            search_line = lines[k].strip()
                            if search_line:
                                # Check for date
                                if not date_match:
                                    date_match = self.extract_date_from_line_rtl(search_line)
                                
                                # Collect description (skip amount and date lines)
                                if (self.extract_single_amount_rtl(search_line) is None and 
                                    not self.extract_date_from_line_rtl(search_line) and
                                    len(search_line) > 3):  # Skip very short lines
                                    description_lines.append(search_line)
                        
                        if date_match:
                            current_date = self.date_to_datetime(date_match)
                    
                            if not current_date:
                                continue

                            
                            # Check for monetary amounts in current and next few lines
                            table_config = self.config.get("transaction_table", {})
                            search_lines_count = table_config.get("search_lines_after", 10)
                            search_lines = lines[i:i + search_lines_count]
                            full_text = ' '.join(search_lines)

                            # Amount detection
                            has_amount = self.detect_amounts_in_text(full_text, currency_symbols)
                    
                            if has_amount:
                                transaction = self.parse_transaction_line_rtl(lines, i, language)
                                if transaction:
                                    # Check for duplicate transactions
                                    if transactions:
                                        last_transaction = transactions[-1]
                                        if (last_transaction['date'] == transaction['date'] and 
                                            last_transaction['description'] == transaction['description'] and
                                            abs(float(last_transaction['balance']) - float(transaction['balance'])) < 0.01):
                                            continue
                                    transactions.append(transaction)
                                    last_valid_date = current_date
                    
        return transactions

    

    def detect_amounts_in_text(self, text, currency_symbols):
        """Detect if text contains monetary amounts"""
        # Check for currency symbols
        for currency in currency_symbols:
            if currency in text:
                return True
        
        # Check for numbers that could be amounts (3+ digits)
        words = text.split()
        for word in words:
            # Remove common punctuation
            clean_word = word.replace(',', '').replace('.', '').replace('-', '')
            if clean_word.isdigit() and len(clean_word) >= 3:
                return True
        
        return False

    
    def extract_amounts(self, full_text):
        """
        Extract first 2 amounts from text with special formatting rules.
        Rules:
        - Amounts must have decimal point with at least 1 digit after
        - If first negative, second positive: add 0.00 between them
        - If both positive: add 0.00 at front, return as [0.00, third_amount, second_amount]
        """
        # Pattern: amounts with decimal point and at least 1 digit after
        # Use negative lookbehind and lookahead to avoid partial matches
        # Look for amounts that are closer together (within reasonable distance)
        
        # Find all amounts first
        all_amounts = re.findall(r'(?<!\d)-?\d{1,3}(?:,\d{3})*\.\d+(?!\d)', full_text)
        
        # Look for consecutive amounts (within a reasonable character distance)
        rough_amounts = []
        for i in range(len(all_amounts) - 1):
            current_pos = full_text.find(all_amounts[i])
            next_pos = full_text.find(all_amounts[i + 1], current_pos + len(all_amounts[i]))
            
            # If the next amount is within 50 characters, consider them consecutive
            if next_pos - current_pos - len(all_amounts[i]) <= 50:
                rough_amounts = [all_amounts[i], all_amounts[i + 1]]
                break
        
        # If no consecutive amounts found, fall back to first 2
        if not rough_amounts:
            rough_amounts = all_amounts[:2]
        
        
        if len(rough_amounts) < 2:
            return []
        
        # Convert to float
        amounts = [float(cell.replace(',', '')) for cell in rough_amounts]
        
        first_amount = amounts[0]
        second_amount = amounts[1]
        
        # Apply rules
        if first_amount < 0 and second_amount > 0:
            # First negative, second positive: add 0.00 between
            result = [first_amount, 0.00, second_amount]
        elif first_amount > 0 and second_amount > 0:
            # Both positive: add 0.00 at front, larger amount goes last
            smaller = min(first_amount, second_amount)
            larger = max(first_amount, second_amount)
            result = [0.00, smaller, larger]
        else:
            # Other cases (both negative, or first positive second negative)
            result = amounts
        
        return result

    
    # def parse_transaction_line(self, lines, line_index, language):
    def parse_transaction_line(self, lines, line_index, language, different_amount_format):
        """Parse a single transaction line"""
        try:
            current_line = lines[line_index]
            
            # Extract date
            date_str = self.extract_date_from_text(current_line)
            if not date_str:
                return None
            
            # Get search configuration
            table_config = self.config.get("transaction_table", {})
            search_lines_count = table_config.get("search_lines_after", 10)
            search_lines = lines[line_index:line_index + search_lines_count]
            full_text = ' '.join(search_lines)

            if(different_amount_format):
                amounts = self.extract_amounts(full_text)
            else:
                # Extract amounts
                # Extract amounts using patterns
                rough_amounts = re.findall(r'[\d,]+\.?\d*\s*SAR', full_text)
                # rough_amounts = self.extract_all_amounts(full_text)

                amounts = [float(cell.replace(' SAR', '').replace(',', '')) for cell in rough_amounts]

            
            if not amounts:
                return None
            
            # Parse amounts
            debit_amount, credit_amount, balance = self.parse_amount_sequence(amounts)
            
            if debit_amount == 0 and credit_amount == 0:
                return None
            
            # Extract description
            description = self.extract_transaction_description(search_lines, amounts)
            
            if not description.strip():
                return None
            
            return {
                'date': date_str,
                'description': description,
                'debit': debit_amount,
                'credit': credit_amount,
                'balance': balance
            }
            
        except Exception as e:
            print(f"Error parsing transaction line: {e}")
            return None



    

    def parse_transaction_line_rtl(self, lines, line_index, language):
        """Parse a single transaction line"""
        try:
            current_line = lines[line_index]
            
            # Get search configuration
            table_config = self.config.get("transaction_table", {})
            search_lines_count = table_config.get("search_lines_after", 10)
            search_lines = lines[line_index:line_index + search_lines_count]
            full_text = ' '.join(search_lines)

            
            date_str = self. extract_date_from_line_rtl(full_text)
            if not date_str:
                return None
            
            # Extract amounts
            # Extract amounts using patterns
            rough_amounts = re.findall(r'[\d,]+\.?\d*\s*SAR', full_text)
            # rough_amounts = self.extract_all_amounts(full_text)

            amounts = [float(cell.replace(' SAR', '').replace(',', '')) for cell in rough_amounts]

            
            if not amounts:
                return None
            
            # Parse amounts
            balance, credit_amount, debit_amount = self.parse_amount_sequence(amounts)
            
            if debit_amount == 0 and credit_amount == 0:
                return None

            description = self.extract_rtl_transaction_description(search_lines, amounts, date_str)
            
            if not description.strip():
                return None
            
            return {
                'date': date_str,
                'description': description,
                'debit': debit_amount,
                'credit': credit_amount,
                'balance': balance
            }
            
        except Exception as e:
            print(f"Error parsing transaction line: {e}")
            return None



    def extract_all_amounts(self, text):
        """Extract all amounts from text"""
        amounts = []
        amount_config = self.config.get("amount_patterns", {})
        currency_symbols = amount_config.get("currency_symbols", ["SAR"])
        
        # Method 1: Look for currency symbols
        for currency in currency_symbols:
            if currency in text:
                parts = text.split(currency)
                for i, part in enumerate(parts[:-1]):
                    # Find the last number in this part
                    words = part.split()
                    for word in reversed(words):
                        clean_word = self.clean_number_string(word)
                        if clean_word and self.is_valid_amount(clean_word):
                            amounts.append(float(clean_word))
                            break
        
        # Method 2: Look for standalone numbers (3+ digits)
        words = text.split()
        for word in words:
            clean_word = self.clean_number_string(word)
            if clean_word and self.is_valid_amount(clean_word) and len(clean_word.replace('.', '').replace(',', '')) >= 3:
                amounts.append(float(clean_word))
        
        # Remove duplicates and sort
        amounts = list(set(amounts))
        amounts.sort()
        
        return amounts

    def clean_number_string(self, text):
        """Clean a string to extract just the number"""
        if not text:
            return None
        
        # Remove common non-numeric characters except digits, dots, commas, and minus
        cleaned = ""
        for char in text:
            if char.isdigit() or char in ".,-":
                cleaned += char
        
        # Handle comma-separated numbers
        if "," in cleaned:
            cleaned = cleaned.replace(",", "")
        
        return cleaned if cleaned else None

    def is_valid_amount(self, text):
        """Check if text represents a valid amount"""
        if not text:
            return False
        
        try:
            # Remove commas and try to convert to float
            clean_text = text.replace(",", "")
            amount = float(clean_text)
            return amount > 0  # Only positive amounts
        except ValueError:
            return False

    def parse_amount_sequence(self, amounts):
        """Parse amounts into debit, credit, and balance based on sequence"""
        if not amounts:
            return 0, 0, 0
        
        if len(amounts) >= 3:
            return amounts[0], amounts[1], amounts[2]
        elif len(amounts) == 2:
            if amounts[0] < amounts[1]:
                return amounts[0], 0, amounts[1]
            else:
                return amounts[0], 0, amounts[1]
        elif len(amounts) == 1:
            return 0, 0, amounts[0]
        
        return 0, 0, 0

    def extract_transaction_description(self, search_lines, amounts):
        """Extract transaction description from search lines"""
        description_parts = []
        
        for line in search_lines:
            # Remove amounts and dates from description
            clean_line = line
            for amount in amounts:
                clean_line = clean_line.replace(str(amount), "")
            clean_line = self.remove_dates_from_text(clean_line)
            
            # Remove currency symbols
            amount_config = self.config.get("amount_patterns", {})
            currency_symbols = amount_config.get("currency_symbols", ["SAR"])
            for currency in currency_symbols:
                clean_line = clean_line.replace(currency, "")
            
            # Clean up extra whitespace
            clean_line = ' '.join(clean_line.split())
            
            if clean_line.strip():
                description_parts.append(clean_line.strip())
        
        # Combine description parts, limit length
        description = ' '.join(description_parts[:3])  # Take first 3 parts
        return description[:100]  # Limit to 100 characters


    def extract_rtl_transaction_description(self, search_lines, amounts, date_str):
        """Extract and clean transaction description from search_lines"""
        # Skip first 3 amount lines, then collect until date is found
        amount_lines_skipped = 0
        description_lines = []
        
        for line in search_lines:
            # Skip first 3 amount lines (lines containing SAR)
            if 'SAR' in line and amount_lines_skipped < 3:
                amount_lines_skipped += 1
                continue
            
            # After skipping 3 amount lines, collect everything until exact date is found
            if amount_lines_skipped >= 3:
                if date_str in line:
                    # Include the line with date_str and break
                    description_lines.append(line)
                    break
                description_lines.append(line)
        
        # Join lines with spaces only
        description = ' '.join(line for line in description_lines if line.strip())
        
        return description



    

    def remove_dates_from_text(self, text):
        """Remove date patterns from text"""
        words = text.split()
        filtered_words = []
        
        for word in words:
            # Skip if it looks like a date
            if '/' in word or '-' in word:
                parts = word.replace('/', '-').split('-')
                if len(parts) == 3:
                    try:
                        # Check if all parts are numbers
                        if all(part.isdigit() for part in parts):
                            continue  # Skip this word
                    except:
                        pass
            filtered_words.append(word)
        
        return ' '.join(filtered_words)

    def analyze_monthly_transactions(self, transactions):
        """Analyze transactions by month with opening and closing balances (separated by year)"""
        monthly_stats = defaultdict(lambda: {
            'count': 0,
            'total_debit': 0,
            'total_credit': 0,
            'opening_balance': None,
            'closing_balance': None,
            'minimum_balance': None,
            'transactions': [],
            'international_inward_count': 0,
            'international_outward_count': 0,
            'international_inward_total': 0,
            'international_outward_total': 0
        })
        
        for transaction in transactions:
            try:
                date_str = transaction['date']
                date_obj = self.date_to_datetime(date_str)
                
                if not date_obj:
                    continue
                
                year_month = date_obj.strftime('%Y-%m')

                monthly_stats[year_month]['count'] += 1
                monthly_stats[year_month]['total_debit'] += float(transaction.get('debit', 0))
                monthly_stats[year_month]['total_credit'] += float(transaction.get('credit', 0))
                monthly_stats[year_month]['transactions'].append(transaction)

                # Check for international transactions (IPS in description)
                description = transaction.get('description', '')
                if 'IPS' in description:
                    debit_amount = float(transaction.get('debit', 0))
                    credit_amount = float(transaction.get('credit', 0))
                    
                    if credit_amount > 0:  # Inward transaction
                        monthly_stats[year_month]['international_inward_count'] += 1
                        monthly_stats[year_month]['international_inward_total'] += credit_amount
                    
                    if debit_amount > 0:  # Outward transaction
                        monthly_stats[year_month]['international_outward_count'] += 1
                        monthly_stats[year_month]['international_outward_total'] += debit_amount
                
            except Exception as e:
                print(f"Error processing transaction date: {e}")
                continue

        # Calculate opening balance, closing balance, and minimum balance for each month
        for year_month, stats in monthly_stats.items():
            transactions_in_month = stats['transactions']
            if transactions_in_month:
                # Sort transactions by date
                transactions_in_month.sort(key=lambda x: self.date_to_datetime(x['date']))
                # First transaction of month gives opening balance (balance before this transaction)
                first_transaction = transactions_in_month[0]
                first_balance = float(first_transaction.get('balance', 0))
                first_debit = float(first_transaction.get('debit', 0))
                first_credit = float(first_transaction.get('credit', 0))
                # Opening balance = current balance - credit + debit
                stats['opening_balance'] = first_balance - first_credit + first_debit
                # Last transaction of month gives closing balance
                last_transaction = transactions_in_month[-1]
                stats['closing_balance'] = float(last_transaction.get('balance', 0))
                # Calculate minimum balance for the month
                balances = [float(t.get('balance', 0)) for t in transactions_in_month]
                stats['minimum_balance'] = min(balances) if balances else None
                # Correct net_change and fluctuation calculations
                inflow = stats['total_credit']
                outflow = abs(stats['total_debit'])
                net_change = inflow - outflow
                stats['net_change'] = net_change
                if stats['opening_balance'] != 0 and stats['opening_balance'] is not None:
                    stats['fluctuation'] = (net_change / stats['opening_balance']) * 100
                else:
                    stats['fluctuation'] = 0
            else:
                stats['net_change'] = 0
                stats['fluctuation'] = 0

                
        
        return dict(monthly_stats)

    def detect_language(self, text):
        """Detect if text is primarily Arabic or English"""
        arabic_chars = len(re.findall(r'[\u0600-\u06FF]', text))
        english_chars = len(re.findall(r'[a-zA-Z]', text))
        
        return 'arabic' if arabic_chars > english_chars else 'english'

    def process_bank_statement(self, pdf_path):
        """Main method to process bank statement PDF"""
        print(f"Processing bank statement: {pdf_path}")

        # Extract text from PDF
        # text_pages, images = self.extract_text_from_pdf(pdf_path)
        text_pages = self.extract_text_from_pdf(pdf_path)
        
        if not text_pages:
            return {"error": "Could not extract text from PDF"}
        
        # NEW: Extract header from second page
        header_info = self.extract_header_from_second_page(text_pages)

        is_LTR = header_info["is_LTR"]
        different_amount_format = header_info["different_amount_format"]
        

        # Detect language from first page
        language = self.detect_language(text_pages[0])
        
        # Extract account information from first page
        account_info = self.extract_account_info(text_pages[0], language, different_amount_format)
        
        
        if not text_pages:
            return {"error": "Could not extract text from PDF"}



        if is_LTR:
            # Extract all transactions for LTR (Left-to-Right) language
            transactions = self.extract_transactions(text_pages, language, different_amount_format)
        else:
            # Extract all transactions for RTL (Right-to-Left) language
            transactions = self.extract_rtl_transactions(text_pages, language)
        
        
        # Analyze monthly transactions
        monthly_analysis = self.analyze_monthly_transactions(transactions)
        # Calculate analytics
        analytics = self.calculate_analytics(monthly_analysis)
        # Compile results
        results = {
            'account_info': account_info,
            'total_transactions': len(transactions),
            'transactions': transactions,
            'monthly_analysis': monthly_analysis,
            'pages_processed': len(text_pages),
            'analytics': analytics
        }
        
        return results




    def print_summary(self, results):
        """Print a summary of extracted information"""

        account_info = results.get('account_info', {})
            
        print(f"Customer Name: {account_info.get('customer_name', 'N/A')}\n")
        print(f"City: {account_info.get('city', 'N/A')}\n")
        print(f"Account Number: {account_info.get('account_number', 'N/A')}\n")
        print(f"IBAN Number: {account_info.get('iban_number', 'N/A')}\n")
        print(f"Opening Balance: {account_info.get('opening_balance', 'N/A')}\n")
        print(f"Closing Balance: {account_info.get('closing_balance', 'N/A')}\n")
        print(f"Financial Period: {account_info.get('financial_period', 'N/A')}\n")
        
        print(f"Pages Processed: {results.get('pages_processed', 'N/A')}")
        print(f"Total Transactions: {results.get('total_transactions', 0)}")

        # Monthly analysis summary
        monthly_analysis = results.get('monthly_analysis', {})
        if monthly_analysis:
            print("\nMONTHLY ANALYSIS:\n")
            print("-" * 30 + "\n")
            for month, stats in monthly_analysis.items():
                print(f"\n{month}:\n")
                print(f"  Transaction Count: {stats['count']}\n")
                print(f"  Total Credits: {stats['total_credit']:.2f}\n")
                print(f"  Total Debits: {stats['total_debit']:.2f}\n")
                opening_bal = f"{stats['opening_balance']:.2f}" if stats['opening_balance'] is not None else 'N/A'
                closing_bal = f"{stats['closing_balance']:.2f}" if stats['closing_balance'] is not None else 'N/A'
                minimum_bal = f"{stats['minimum_balance']:.2f}" if stats['minimum_balance'] is not None else 'N/A'
                    
                print(f"  Opening Balance: {opening_bal}\n")
                print(f"  Closing Balance: {closing_bal}\n")
                print(f"  Minimum Balance: {minimum_bal}\n")
                print(f"  International Inward: {stats['international_inward_count']} transactions, Total: {stats['international_inward_total']:.2f}\n")
                print(f"  International Outward: {stats['international_outward_count']} transactions, Total: {stats['international_outward_total']:.2f}\n")

        # Overdraft analysis summary
        overdraft_analysis = results.get('overdraft_analysis', {})
        if overdraft_analysis:
            print("\nOVERDRAFT ANALYSIS:\n")
            print("-" * 30 + "\n")
            for month, stats in overdraft_analysis.items():
                if stats['total_overdraft_occurrences'] > 0:
                    print(f"\n{month}:\n")
                    print(f"  Overdraft Occurrences: {stats['total_overdraft_occurrences']}\n")
                    print(f"  Total Overdraft Days: {stats['total_overdraft_days']}\n")
                    print(f"  Maximum Overdraft Amount: {stats['max_overdraft_amount']:.2f}\n")
                        
                    # Show overdraft periods
                    for i, period in enumerate(stats['overdraft_periods'], 1):
                        print(f"    Period {i}: {period['start_date'].strftime('%Y-%m-%d')} to {period['end_date'].strftime('%Y-%m-%d')} ({period['duration_days']} days)\n")
                        if period.get('note'):
                            print(f"      Note: {period['note']}\n")
            




    def calculate_analytics(self, monthly_analysis):
        """Calculate analytics for the frontend from monthly_analysis"""
        if not monthly_analysis:
            return {
                'average_fluctuation': 0,
                'net_cash_flow_stability': 0,
                'total_foreign_transactions': 0,
                'total_foreign_amount': 0,
                'overdraft_frequency': 0,
                'overdraft_total_days': 0
            }
        months = list(monthly_analysis.values())
        # Average fluctuation: mean of all months' fluctuation
        fluctuation_values = [m.get('fluctuation', 0) for m in months if m.get('fluctuation') is not None]
        if fluctuation_values:
            avg_fluctuation = sum(fluctuation_values) / len(fluctuation_values)
        else:
            avg_fluctuation = 0
        # Cash Flow Stability: STDEV(net change) / AVERAGE(net change)
        net_changes = [m.get('net_change', 0) for m in months if m.get('net_change') is not None]
        if net_changes and len(net_changes) > 1:
            mean_net_change = sum(net_changes) / len(net_changes)
            variance = sum((x - mean_net_change) ** 2 for x in net_changes) / (len(net_changes) - 1)
            stdev = variance ** 0.5
            cash_flow_stability = stdev / mean_net_change if mean_net_change != 0 else 0
        else:
            cash_flow_stability = 0
        # Total foreign transactions and amount: sum of all months
        total_foreign_transactions = sum(m.get('international_inward_count', 0) for m in months)
        total_foreign_amount = sum(m.get('international_inward_total', 0) for m in months)
        # Sum and average of total inflow and outflow for all months
        inflows = [m.get('total_credit', 0) for m in months if m.get('total_credit') is not None]
        outflows = [abs(m.get('total_debit', 0)) for m in months if m.get('total_debit') is not None]
        sum_inflow = sum(inflows)
        sum_outflow = sum(outflows)
        avg_inflow = sum_inflow / len(inflows) if inflows else 0
        avg_outflow = sum_outflow / len(outflows) if outflows else 0
        return {
            'average_fluctuation': avg_fluctuation,
            'net_cash_flow_stability': cash_flow_stability,
            'total_foreign_transactions': total_foreign_transactions,
            'total_foreign_amount': total_foreign_amount,
            'sum_total_inflow': sum_inflow,
            'sum_total_outflow': sum_outflow,
            'avg_total_inflow': avg_inflow,
            'avg_total_outflow': avg_outflow,
            'overdraft_frequency': 0,
            'overdraft_total_days': 0
        } 





