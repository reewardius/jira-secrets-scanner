#!/usr/bin/env python3
"""
Scans Jira projects and issues for exposed secrets.
Authenticates via Atlassian email and API token.
Exports findings to a formatted Excel report.
"""

import requests
from requests.auth import HTTPBasicAuth
import json
import sys
import argparse
import os
import re
import shutil
from io import BytesIO
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from datetime import datetime
from typing import List, Dict, Tuple
import warnings

import yaml

# OCR support (optional)
try:
    from PIL import Image
    import pytesseract
    os.environ["TESSDATA_PREFIX"] = "/usr/local/share/tessdata/"
    warnings.simplefilter('ignore', Image.DecompressionBombWarning)
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

# PDF support (optional)
try:
    import fitz  # PyMuPDF
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

# DOCX support (optional)
try:
    import docx as python_docx
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

# AWS SES for email delivery
try:
    import boto3
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders
    AWS_SES_AVAILABLE = True
except ImportError:
    AWS_SES_AVAILABLE = False


def load_env_file(env_path='.env'):
    """
    Load variables from a .env file.
    """
    env_vars = {}
    env_file = Path(env_path)
    
    if env_file.exists():
        with open(env_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key.strip()] = value.strip()
    
    return env_vars


def normalize_jira_url(url):
    """
    Normalize Jira URL by stripping trailing slash.
    """
    return url.rstrip('/')


def load_secret_patterns(patterns_file='secret_patterns.txt'):
    """
    Load secret detection patterns from file.
    Format: Name:::Regex:::GroupIndex
    """
    patterns = []
    
    if not Path(patterns_file).exists():
        print(f"⚠️  Patterns file {patterns_file} not found. Using built-in fallback patterns.")
        # Built-in fallback patterns
        return [
            ('AWS Access Key ID', r'(?:^|[^A-Za-z0-9])((AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)(?!([A-Z0-9])\3{5,})[A-Z0-9]{16})(?:[^A-Za-z0-9]|$)', 1),
            ('GitHub Personal Access Token', r'(?:^|[^a-z0-9_])(ghp_[0-9a-zA-Z]{36})(?:[^a-zA-Z0-9]|$)', 1),
            ('Atlassian API Token', r'(?:^|[^A-Z])(ATATT[a-zA-Z0-9\-_]{28,})(?:[^a-zA-Z0-9\-_]|$)', 1),
        ]
    
    with open(patterns_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and ':::' in line:
                parts = line.split(':::')
                if len(parts) >= 3:
                    name = parts[0].strip()
                    regex = parts[1].strip()
                    group_index = int(parts[2].strip())
                    patterns.append((name, regex, group_index))
    
    return patterns


def load_trufflehog_patterns(patterns_file):
    """
    Loads TruffleHog v3 YAML patterns and converts them to internal format.

    TruffleHog v3 format:
        - name: rule_name
          keywords:
            - keyword1
          regex:
            rule_name: <regex>

    Converts to: (name, regex, group_index=0)
    """
    patterns = []

    if not Path(patterns_file).exists():
        print(f"⚠️  TruffleHog patterns file '{patterns_file}' not found.")
        return patterns

    try:
        with open(patterns_file, 'r', encoding='utf-8') as f:
            rules = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"⚠️  Failed to parse TruffleHog YAML file: {e}")
        return patterns

    if not isinstance(rules, list):
        print(f"⚠️  Expected a list of rules in '{patterns_file}', got {type(rules).__name__}")
        return patterns

    for rule in rules:
        name = rule.get('name', 'unknown')
        regexes = rule.get('regex', {})

        if not isinstance(regexes, dict):
            continue

        for regex_name, regex_pattern in regexes.items():
            if not regex_pattern:
                continue
            try:
                re.compile(regex_pattern)
                patterns.append((f"{name} ({regex_name})", regex_pattern, 0))
            except re.error as e:
                print(f"⚠️  Invalid regex in TruffleHog rule '{name}': {e}")
                continue

    return patterns


def scan_text_for_secrets(text: str, patterns: List[Tuple[str, str, int]]) -> List[Dict]:
    """
    Scan text for secrets using the provided regex patterns.

    Returns:
        List of dicts with keys: secret_type, secret_value, context
    """
    findings = []
    
    for pattern_name, pattern_regex, group_index in patterns:
        try:
            matches = re.finditer(pattern_regex, text, re.MULTILINE | re.IGNORECASE)
            for match in matches:
                if group_index < len(match.groups()) + 1:
                    secret_value = match.group(group_index) if group_index > 0 else match.group(0)
                    
                    # Extract surrounding context (50 chars before and after)
                    start = max(0, match.start() - 50)
                    end = min(len(text), match.end() + 50)
                    context = text[start:end].replace('\n', ' ').replace('\r', '').strip()
                    
                    findings.append({
                        'secret_type': pattern_name,
                        'secret_value': secret_value,
                        'context': context
                    })
        except re.error as e:
            print(f"⚠️  Invalid regex pattern '{pattern_name}': {e}")
            continue
    
    return findings


def get_issue_attachments(email, api_token, jira_url, issue_key):
    """
    Fetches the list of attachments for a given Jira issue.
    Returns a list of attachment metadata dicts.
    """
    jira_url = normalize_jira_url(jira_url)
    url = f"{jira_url}/rest/api/2/issue/{issue_key}?fields=attachment"
    auth = HTTPBasicAuth(email, api_token)
    headers = {"Accept": "application/json"}

    try:
        response = requests.get(url, headers=headers, auth=auth, timeout=30)
        if response.status_code == 200:
            fields = response.json().get("fields", {})
            return fields.get("attachment", [])
    except Exception as e:
        print(f"⚠️  Failed to fetch attachments for {issue_key}: {e}")
    return []


def extract_text_from_attachment(attachment, email, api_token, max_size_bytes=None):
    """
    Downloads an attachment and extracts text from it.
    Supports: plain text, JSON/YAML/XML, DOCX, PDF, images (via OCR).

    Returns:
        tuple(text: str, ext: str)
    """
    att_title = attachment.get("filename", "unknown")
    download_url = attachment.get("content", "")
    file_size = attachment.get("size", 0)
    ext = os.path.splitext(att_title)[1].lstrip(".").lower()

    if max_size_bytes and file_size > max_size_bytes:
        return "", ext

    # Only process file types we can handle
    supported_text_exts = {
        "txt", "log", "md", "rst", "conf", "cfg", "ini", "properties", "env",
        "json", "xml", "yaml", "yml", "toml", "csv", "tsv",
        "py", "sh", "bash", "bat", "ps1", "js", "ts", "java", "go", "rb",
        "php", "cs", "c", "cpp", "h", "sql", "tf", "hcl", "dockerfile",
        "html", "htm", "css",
    }
    supported_image_exts = {"png", "jpg", "jpeg", "gif", "bmp", "tiff"}

    is_text = ext in supported_text_exts
    is_image = ext in supported_image_exts
    is_pdf = ext == "pdf"
    is_docx = ext == "docx"

    if not (is_text or is_image or is_pdf or is_docx):
        return "", ext

    try:
        auth = HTTPBasicAuth(email, api_token)
        response = requests.get(download_url, auth=auth, timeout=30)
        response.raise_for_status()
        content = response.content

        if is_text:
            return content.decode("utf-8", errors="ignore"), ext

        if is_docx and DOCX_AVAILABLE:
            doc = python_docx.Document(BytesIO(content))
            return "\n".join(p.text for p in doc.paragraphs), ext

        if is_pdf and PDF_AVAILABLE:
            pdf_doc = fitz.open(stream=content, filetype="pdf")
            return "\n".join(page.get_text() for page in pdf_doc), ext

        if is_image and OCR_AVAILABLE:
            img = Image.open(BytesIO(content))
            return pytesseract.image_to_string(img), ext

    except Exception as e:
        print(f"⚠️  Error extracting text from attachment '{att_title}': {e}")

    return "", ext


def get_jira_projects(email, api_token, jira_url):
    """
    Fetch all accessible Jira projects with full details.
    """
    jira_url = normalize_jira_url(jira_url)
    url = f"{jira_url}/rest/api/3/project/search"
    auth = HTTPBasicAuth(email, api_token)
    params = {'expand': 'description,lead,url,insight'}
    headers = {"Accept": "application/json"}
    
    all_projects = []
    start_at = 0
    max_results = 50
    
    try:
        while True:
            params['startAt'] = start_at
            params['maxResults'] = max_results
            
            response = requests.get(url, headers=headers, auth=auth, params=params)
            
            if response.status_code == 200:
                data = response.json()
                projects = data.get('values', [])
                all_projects.extend(projects)
                
                if data.get('isLast', True):
                    break
                start_at += max_results
            elif response.status_code == 401:
                print("❌ Authentication failed. Check your email and API token.")
                return None
            elif response.status_code == 403:
                print("❌ Access denied. Check your permissions.")
                return None
            else:
                print(f"❌ Error: {response.status_code}")
                print(f"Response: {response.text}")
                return None
        
        return all_projects
    except requests.exceptions.RequestException as e:
        print(f"❌ Request failed: {e}")
        return None


def get_project_issues(email, api_token, jira_url, project_key, max_issues=0, verbose=False):
    """
    Fetch issues for a given project.
    Tries multiple API endpoints for compatibility.
    max_issues=0 means fetch all issues.
    """
    jira_url = normalize_jira_url(jira_url)
    auth = HTTPBasicAuth(email, api_token)
    headers = {"Accept": "application/json"}
    
    all_issues = []
    
    # If max_issues=0, set a large effective limit
    unlimited = (max_issues == 0)
    if unlimited:
        effective_limit = 999999  # Effectively unlimited
    else:
        effective_limit = max_issues
    
    if verbose:
        limit_text = "all" if unlimited else str(max_issues)
        print(f"   🔍 Fetching issues for project {project_key} (limit: {limit_text})")
    
    # Method 1: Board API (Software/Scrum/Kanban projects)
    if verbose:
        print(f"      → Method 1: Board API")
    
    try:
        # Find boards for the project
        board_endpoint = f"{jira_url}/rest/agile/1.0/board"
        params = {'projectKeyOrId': project_key}
        response = requests.get(board_endpoint, headers=headers, auth=auth, params=params, timeout=30)
        
        if verbose:
            print(f"         Board search: {response.status_code}")
        
        if response.status_code == 200:
            boards_data = response.json()
            boards = boards_data.get('values', [])
            
            if verbose:
                print(f"         Boards found: {len(boards)}")
            
            if boards:
                board_id = boards[0]['id']
                board_name = boards[0].get('name', 'Unknown')
                if verbose:
                    print(f"         Using board: {board_name} (ID: {board_id})")
                
                # Fetch issues via board with pagination
                start_at = 0
                max_results = 50
                
                while len(all_issues) < effective_limit:
                    issues_endpoint = f"{jira_url}/rest/agile/1.0/board/{board_id}/issue"
                    
                    # Determine how many issues to request in this iteration
                    if unlimited:
                        request_limit = max_results
                    else:
                        request_limit = min(max_results, effective_limit - len(all_issues))
                    
                    params = {
                        'startAt': start_at,
                        'maxResults': request_limit,
                        'fields': 'summary,description,comment,creator,created,updated'
                    }
                    
                    response = requests.get(issues_endpoint, headers=headers, auth=auth, params=params, timeout=30)
                    
                    if response.status_code == 200:
                        data = response.json()
                        issues = data.get('issues', [])
                        total = data.get('total', 0)
                        
                        if not issues:
                            break
                        
                        all_issues.extend(issues)
                        
                        if verbose:
                            progress = f"{len(all_issues)}/{total}" if not unlimited else f"{len(all_issues)}"
                            print(f"         Loaded {progress} issues")
                        
                        # Check exit conditions
                        if len(issues) < max_results:  # Last page
                            break
                        if len(all_issues) >= total:  # Got all issues
                            break
                        if not unlimited and len(all_issues) >= effective_limit:  # Reached limit
                            break
                        
                        start_at += max_results
                    else:
                        if verbose:
                            print(f"         ⚠️  Pagination error: {response.status_code}")
                        break
                
                if all_issues:
                    if verbose:
                        print(f"         ✅ Fetched via Board API: {len(all_issues)} issues")
                    return all_issues
                        
    except Exception as e:
        if verbose:
            print(f"         ❌ Board API exception: {e}")
    
    # Method 2: JQL Search
    if verbose:
        print(f"      → Method 2: JQL Search API")
    
    jql_variants = [
        f'project = "{project_key}" ORDER BY created DESC',
        f'project = {project_key} ORDER BY created DESC',
        f'project = "{project_key}"',
        f'project = {project_key}',
    ]
    
    for jql in jql_variants:
        try:
            all_issues = []
            start_at = 0
            max_results = 50
            
            # Try API v2 (broader compatibility)
            endpoint = f"{jira_url}/rest/api/2/search"
            
            if verbose:
                print(f"         Trying JQL: {jql}")
            
            while len(all_issues) < effective_limit:
                # Determine how many issues to request
                if unlimited:
                    request_limit = max_results
                else:
                    request_limit = min(max_results, effective_limit - len(all_issues))
                
                params = {
                    'jql': jql,
                    'startAt': start_at,
                    'maxResults': request_limit,
                    'fields': 'summary,description,comment,creator,created,updated'
                }
                
                response = requests.get(endpoint, headers=headers, auth=auth, params=params, timeout=30)
                
                if response.status_code == 200:
                    data = response.json()
                    total = data.get('total', 0)
                    issues = data.get('issues', [])
                    
                    if not issues:
                        break
                    
                    all_issues.extend(issues)
                    
                    if verbose and start_at == 0:
                        print(f"         Total issues in project: {total}")
                    
                    # Check exit conditions
                    if len(issues) < max_results:  # Last page
                        break
                    if len(all_issues) >= total:  # Got all issues
                        break
                    if not unlimited and len(all_issues) >= effective_limit:  # Reached limit
                        break
                    
                    start_at += max_results
                else:
                    if verbose:
                        error_msg = response.text[:150] if len(response.text) > 150 else response.text
                        print(f"         ⚠️  Status {response.status_code}: {error_msg}")
                    break
            
            if all_issues:
                if verbose:
                    print(f"         ✅ Fetched via JQL: {len(all_issues)} issues")
                return all_issues
                
        except Exception as e:
            if verbose:
                print(f"         ❌ Exception: {e}")
            continue
    
    # Method 3: Direct project issues access (older Jira versions)
    if verbose:
        print(f"      → Method 3: Project Issues API")
    
    try:
        endpoint = f"{jira_url}/rest/api/2/project/{project_key}"
        response = requests.get(endpoint, headers=headers, auth=auth, timeout=30)
        
        if verbose:
            print(f"         Project API: {response.status_code}")
        
        if response.status_code == 200:
            # Project exists but does not expose issues directly
            # Try simple JQL without filters
            endpoint = f"{jira_url}/rest/api/2/search"
            params = {
                'jql': f'key ~ "{project_key}-*"',
                'maxResults': max_results if not unlimited else 50,
                'fields': 'summary,description,comment,creator,created,updated'
            }
            
            response = requests.get(endpoint, headers=headers, auth=auth, params=params, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                issues = data.get('issues', [])
                
                if issues:
                    if verbose:
                        print(f"         ✅ Fetched via key search: {len(issues)} issues")
                    return issues
                    
    except Exception as e:
        if verbose:
            print(f"         ❌ Exception: {e}")
    
    # All methods exhausted
    if verbose:
        print(f"      ❌ All methods returned no results")
        print(f"      ℹ️  Possible reasons:")
        print(f"          - Project is empty (no issues)")
        print(f"          - Insufficient permissions to view issues")
        print(f"          - Project is archived")
        print(f"          - Special project type")
    
    return []


def scan_issue_for_secrets(issue, patterns, jira_url, email=None, api_token=None,
                           scan_attachments=False, max_attachment_size=None):
    """
    Scan a single issue for secrets.
    Optionally scans attachments including images via OCR.

    Args:
        scan_attachments: if True, download and scan attachments
        max_attachment_size: max attachment size in bytes (None = no limit)
    """
    findings = []
    issue_key = issue.get('key', 'UNKNOWN')
    issue_url = f"{jira_url}/browse/{issue_key}"

    fields = issue.get('fields', {})

    # Issue author
    creator = fields.get('creator', {})
    author = creator.get('displayName', 'Unknown') if creator else 'Unknown'
    author_email = creator.get('emailAddress', 'N/A') if creator else 'N/A'

    # Creation date
    created = fields.get('created', 'N/A')

    # Summary
    summary = fields.get('summary', '')

    # Fields to scan
    texts_to_scan = []

    # Summary
    if summary:
        texts_to_scan.append(('Summary', summary))

    # Description
    description = fields.get('description')
    if description:
        if isinstance(description, dict):
            desc_text = extract_text_from_adf(description)
        else:
            desc_text = str(description)
        if desc_text:
            texts_to_scan.append(('Description', desc_text))

    # Comments
    comments = fields.get('comment', {}).get('comments', [])
    for idx, comment in enumerate(comments):
        comment_body = comment.get('body')
        if comment_body:
            if isinstance(comment_body, dict):
                comment_text = extract_text_from_adf(comment_body)
            else:
                comment_text = str(comment_body)
            if comment_text:
                texts_to_scan.append((f'Comment {idx+1}', comment_text))

    # Attachments
    if scan_attachments and email and api_token:
        attachments = get_issue_attachments(email, api_token, jira_url, issue_key)
        for attachment in attachments:
            att_name = attachment.get("filename", "unknown")
            text, ext = extract_text_from_attachment(
                attachment, email, api_token, max_size_bytes=max_attachment_size
            )
            if text:
                texts_to_scan.append((f'Attachment: {att_name}', text))

    # Scan all collected text
    for location, text in texts_to_scan:
        secrets = scan_text_for_secrets(text, patterns)

        for secret in secrets:
            findings.append({
                'project_key': issue_key.split('-')[0],
                'issue_key': issue_key,
                'issue_url': issue_url,
                'summary': summary,
                'author': author,
                'author_email': author_email,
                'created': created,
                'location': location,
                'secret_type': secret['secret_type'],
                'secret_value': secret['secret_value'],
                'context': secret['context']
            })

    return findings


def extract_text_from_adf(adf_content):
    """
    Extract plain text from Atlassian Document Format (ADF).
    """
    if not isinstance(adf_content, dict):
        return str(adf_content)
    
    text_parts = []
    
    def extract_recursive(node):
        if isinstance(node, dict):
            # Collect text nodes
            if 'text' in node:
                text_parts.append(node['text'])
            
            # Recurse into content
            if 'content' in node and isinstance(node['content'], list):
                for child in node['content']:
                    extract_recursive(child)
        elif isinstance(node, list):
            for item in node:
                extract_recursive(item)
    
    extract_recursive(adf_content)
    return ' '.join(text_parts)


def create_secrets_report(findings, filename="jira_secrets_report.xlsx"):
    """
    Create a formatted Excel report with all findings.
    """
    wb = Workbook()
    
    # Sheet 1: Findings
    sheet_secrets = wb.active
    sheet_secrets.title = "Found Secrets"
    
    # Headers
    headers = [
        'Project', 'Issue Key', 'Issue URL', 'Summary', 'Author', 'Author Email',
        'Created', 'Location', 'Secret Type', 'Secret Value', 'Context'
    ]
    
    # Header style
    header_fill = PatternFill(start_color='DC143C', end_color='DC143C', fill_type='solid')
    header_font = Font(bold=True, color='FFFFFF', size=11)
    border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )
    
    for col_num, header in enumerate(headers, 1):
        cell = sheet_secrets.cell(row=1, column=col_num)
        cell.value = header
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = border
    
    # Data rows
    for row_num, finding in enumerate(findings, 2):
        sheet_secrets.cell(row=row_num, column=1).value = finding['project_key']
        sheet_secrets.cell(row=row_num, column=2).value = finding['issue_key']
        
        # URL as hyperlink
        url_cell = sheet_secrets.cell(row=row_num, column=3)
        url_cell.value = finding['issue_url']
        url_cell.hyperlink = finding['issue_url']
        url_cell.font = Font(color='0563C1', underline='single')
        
        sheet_secrets.cell(row=row_num, column=4).value = finding['summary']
        sheet_secrets.cell(row=row_num, column=5).value = finding['author']
        sheet_secrets.cell(row=row_num, column=6).value = finding['author_email']
        sheet_secrets.cell(row=row_num, column=7).value = finding['created']
        sheet_secrets.cell(row=row_num, column=8).value = finding['location']
        sheet_secrets.cell(row=row_num, column=9).value = finding['secret_type']
        
        # Secret value — red font
        secret_cell = sheet_secrets.cell(row=row_num, column=10)
        secret_cell.value = finding['secret_value']
        secret_cell.font = Font(color='DC143C', bold=True)
        
        sheet_secrets.cell(row=row_num, column=11).value = finding['context']
        
        # Apply borders to all cells
        for col_num in range(1, len(headers) + 1):
            sheet_secrets.cell(row=row_num, column=col_num).border = border
    
    # Column widths
    sheet_secrets.column_dimensions['A'].width = 12  # Project
    sheet_secrets.column_dimensions['B'].width = 15  # Issue Key
    sheet_secrets.column_dimensions['C'].width = 45  # URL
    sheet_secrets.column_dimensions['D'].width = 40  # Summary
    sheet_secrets.column_dimensions['E'].width = 20  # Author
    sheet_secrets.column_dimensions['F'].width = 25  # Author Email
    sheet_secrets.column_dimensions['G'].width = 20  # Created
    sheet_secrets.column_dimensions['H'].width = 15  # Location
    sheet_secrets.column_dimensions['I'].width = 30  # Secret Type
    sheet_secrets.column_dimensions['J'].width = 50  # Secret Value
    sheet_secrets.column_dimensions['K'].width = 60  # Context
    
    # Text wrapping
    for row in range(2, len(findings) + 2):
        for col in [4, 10, 11]:  # Summary, Secret, Context
            sheet_secrets.cell(row=row, column=col).alignment = Alignment(wrap_text=True, vertical='top')
    
    # Freeze header row
    sheet_secrets.freeze_panes = 'A2'
    
    # Sheet 2: Statistics
    sheet_stats = wb.create_sheet("Statistics")
    
    # Statistics header
    sheet_stats['A1'] = 'Secret Scanning Statistics'
    sheet_stats['A1'].font = Font(bold=True, size=14)
    
    # Stats data
    stats_data = [
        ['Total Secrets Found:', len(findings)],
        ['Unique Secret Types:', len(set(f['secret_type'] for f in findings))],
        ['Affected Projects:', len(set(f['project_key'] for f in findings))],
        ['Affected Issues:', len(set(f['issue_key'] for f in findings))],
    ]
    
    for idx, (label, value) in enumerate(stats_data, 3):
        sheet_stats.cell(row=idx, column=1).value = label
        sheet_stats.cell(row=idx, column=1).font = Font(bold=True)
        sheet_stats.cell(row=idx, column=2).value = value
    
    # Group by secret type
    sheet_stats.cell(row=len(stats_data) + 5, column=1).value = 'Secrets by Type:'
    sheet_stats.cell(row=len(stats_data) + 5, column=1).font = Font(bold=True, size=12)
    
    secret_types = {}
    for finding in findings:
        secret_type = finding['secret_type']
        secret_types[secret_type] = secret_types.get(secret_type, 0) + 1
    
    row_offset = len(stats_data) + 6
    for idx, (secret_type, count) in enumerate(sorted(secret_types.items(), key=lambda x: x[1], reverse=True), 0):
        sheet_stats.cell(row=row_offset + idx, column=1).value = secret_type
        sheet_stats.cell(row=row_offset + idx, column=2).value = count
    
    sheet_stats.column_dimensions['A'].width = 35
    sheet_stats.column_dimensions['B'].width = 15
    
    wb.save(filename)
    return filename


def send_email_report(report_filename, findings, scan_stats, email_config):
    """
    Send email report via AWS SES
    
    Args:
        report_filename: Path to Excel report file
        findings: List of findings
        scan_stats: Dictionary with scan statistics
        email_config: Dictionary with email configuration:
            - sender: Sender email (must be verified in SES)
            - recipient: Recipient email
            - aws_region: AWS region (default: us-east-1)
    """
    if not AWS_SES_AVAILABLE:
        print("❌ AWS SES not available. Install: pip install boto3")
        return False
    
    sender = email_config.get('sender')
    recipient = email_config.get('recipient')
    aws_region = email_config.get('aws_region', 'us-east-1')
    
    if not sender or not recipient:
        print("❌ Email sender and recipient are required")
        return False
    
    try:
        # Create SES client
        ses_client = boto3.client("ses", region_name=aws_region)
        
        # Prepare subject with CRITICAL prefix
        total_secrets = len(findings)
        if total_secrets > 0:
            subject = f"CRITICAL: Jira Secrets Scanner - {total_secrets} Secret{'s' if total_secrets != 1 else ''} Found"
        else:
            subject = "INFO: Jira Secrets Scanner - No Secrets Found"
        
        # Prepare email body
        body_text = generate_email_body(findings, scan_stats)
        
        # Create email message
        msg = MIMEMultipart()
        msg["From"] = sender
        msg["To"] = recipient
        msg["Subject"] = subject
        
        # Attach body
        msg.attach(MIMEText(body_text, "plain"))
        
        # Attach report directly — no temp copy needed
        if os.path.exists(report_filename):
            with open(report_filename, "rb") as attachment:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(attachment.read())

            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                "attachment; filename=jira_secrets.xlsx",
            )
            msg.attach(part)
        
        # Send email
        response = ses_client.send_raw_email(
            Source=sender,
            Destinations=[recipient],
            RawMessage={"Data": msg.as_string()}
        )
        
        print(f"\n✅ Email sent successfully!")
        print(f"   MessageId: {response['MessageId']}")
        print(f"   From: {sender}")
        print(f"   To: {recipient}")
        print(f"   Subject: {subject}")
        return True
        
    except Exception as e:
        print(f"\n❌ Error sending email: {e}")
        return False


def generate_email_body(findings, scan_stats):
    """
    Generate email body text with scan results
    
    Args:
        findings: List of findings
        scan_stats: Dictionary with scan statistics
    
    Returns:
        String with formatted email body
    """
    timestamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    
    body = f"""Jira Secrets Scanner Report - {timestamp}

Summary Statistics:
* Total Secrets Found: {len(findings)}
* Affected Projects: {len(set(f['project_key'] for f in findings)) if findings else 0}
* Affected Issues: {len(set(f['issue_key'] for f in findings)) if findings else 0}

"""
    
    if findings:
        body += f"""ACTION REQUIRED:
1. Review the attached report immediately
2. Rotate/revoke exposed credentials
3. Implement proper secrets management

The detailed report is attached as XLSX file.

---
This is an automated report generated by Jira Secrets Scanner."""
    else:
        body += f"""RESULT:
No secrets detected in scanned Jira issues.
This is a good security posture!

---
This is an automated report generated by Jira Secrets Scanner."""
    
    return body


def parse_arguments():
    """
    Parse command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description='Scan Jira projects and issues for exposed secrets.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scan all projects
  python jira_scanner.py --env --scan-secrets

  # Scan specific projects
  python jira_scanner.py -e user@company.com -t TOKEN -u URL --scan-secrets --projects PROJ1,PROJ2

  # Limit issues per project
  python jira_scanner.py --env --scan-secrets --max-issues 50

  # Use custom patterns file
  python jira_scanner.py --env --scan-secrets --patterns custom_patterns.txt
        """
    )
    
    parser.add_argument('-e', '--email', type=str, help='Atlassian email')
    parser.add_argument('-t', '--token', type=str, help='Atlassian API token')
    parser.add_argument('-u', '--url', type=str, help='Jira instance URL')
    parser.add_argument('-o', '--output', type=str, help='Output filename')
    parser.add_argument('--env', action='store_true', help='Load credentials from .env file')
    parser.add_argument('--env-file', type=str, default='.env', help='Path to .env file')
    parser.add_argument('--scan-secrets', action='store_true', help='Enable secret scanning')
    parser.add_argument('--patterns', type=str, default='secret_patterns.txt', help='Secret patterns file (Name:::Regex:::GroupIndex format)')
    parser.add_argument('--trufflehog-patterns', type=str, default=None, help='TruffleHog v3 YAML patterns file')
    parser.add_argument('--projects', type=str, help='Comma-separated project keys to scan (default: all)')
    parser.add_argument('--max-issues', type=int, default=0, help='Max issues per project; 0 = unlimited (default: 0)')
    parser.add_argument('-q', '--quiet', action='store_true', help='Suppress per-issue output')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose debug output')
    
    # Attachment scanning
    parser.add_argument('--scan-attachments', action='store_true',
                        help='Scan issue attachments (txt, json, py, pdf, docx, images via OCR)')
    parser.add_argument('--max-attachment-size', type=str, default=None,
                        help='Max attachment size to scan, e.g. 2mb, 500kb (default: no limit)')
    
    # Email notification arguments (if specified, email will be sent automatically)
    parser.add_argument('--email-sender', type=str, help='Sender email address (must be verified in SES)')
    parser.add_argument('--email-recipient', type=str, help='Recipient email address')
    parser.add_argument('--aws-region', type=str, default='us-east-1', help='AWS region for SES (default: us-east-1)')
    
    return parser.parse_args()


def parse_size(size_str):
    """Parse size string like '2mb' or '500kb' to bytes. Returns None if input is None."""
    if not size_str:
        return None
    size_str = size_str.lower().strip()
    match = re.match(r"(\d+)(kb|mb|gb)?", size_str)
    if not match:
        return None
    num = int(match.group(1))
    unit = match.group(2) or "b"
    multipliers = {"b": 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3}
    return num * multipliers.get(unit, 1)


def main():
    """
    Main entry point.
    """
    args = parse_arguments()
    
    print("🔍 Jira Secret Scanner")
    print("="*80)
    
    # Load .env file
    env_vars = {}
    if args.env:
        env_vars = load_env_file(args.env_file)
        if env_vars:
            print(f"✅ Loaded config from {args.env_file}\n")
    
    # Resolve credentials
    email = args.email or env_vars.get('JIRA_EMAIL') or input("Enter your Atlassian email: ").strip()
    api_token = args.token or env_vars.get('JIRA_TOKEN') or input("Enter your API token: ").strip()
    jira_url = args.url or env_vars.get('JIRA_URL') or input("Enter your Jira URL: ").strip()
    
    if not email or not api_token or not jira_url:
        print("❌ Email, token, and URL are required.")
        sys.exit(1)
    
    jira_url = normalize_jira_url(jira_url)
    
    # Fetch projects
    print("\n🔄 Fetching projects...")
    projects = get_jira_projects(email, api_token, jira_url)
    
    if not projects:
        print("❌ Failed to fetch projects.")
        sys.exit(1)
    
    print(f"✅ Projects found: {len(projects)}\n")
    
    # Filter projects if specific keys were provided
    if args.projects:
        selected_keys = [k.strip().upper() for k in args.projects.split(',')]
        projects = [p for p in projects if p.get('key', '').upper() in selected_keys]
        print(f"🎯 Projects selected for scanning: {len(projects)}")
    
    # Secret scanning
    if args.scan_secrets:
        print("\n🔐 Starting secret scan...\n")
        
        # Attachment scanning setup
        scan_attachments = args.scan_attachments
        max_attachment_size = parse_size(args.max_attachment_size)

        if scan_attachments:
            if OCR_AVAILABLE:
                print("🖼️  OCR enabled (Tesseract) — images will be scanned")
            else:
                print("⚠️  OCR unavailable (install Pillow + pytesseract + tesseract)")
            if PDF_AVAILABLE:
                print("📄 PDF support enabled (PyMuPDF)")
            else:
                print("⚠️  PDF support unavailable (install PyMuPDF: pip install pymupdf)")
            if DOCX_AVAILABLE:
                print("📝 DOCX support enabled (python-docx)")
            else:
                print("⚠️  DOCX support unavailable (install python-docx)")
            if max_attachment_size:
                print(f"📦 Max attachment size: {args.max_attachment_size}\n")
            else:
                print("📦 Max attachment size: unlimited\n")

        # Load patterns
        if args.trufflehog_patterns:
            patterns = load_trufflehog_patterns(args.trufflehog_patterns)
            print(f"✅ Patterns loaded (TruffleHog): {len(patterns)}\n")
        else:
            patterns = load_secret_patterns(args.patterns)
            print(f"✅ Patterns loaded: {len(patterns)}\n")
        
        all_findings = []
        total_issues_scanned = 0
        
        for project in projects:
            project_key = project.get('key', 'UNKNOWN')
            project_name = project.get('name', 'Unknown')
            
            print(f"📊 Scanning project: {project_key} - {project_name}")
            
            # Fetch issues
            issues = get_project_issues(email, api_token, jira_url, project_key, args.max_issues, args.verbose)
            print(f"   Issues fetched: {len(issues)}")
            total_issues_scanned += len(issues)
            
            # Scan each issue
            for issue in issues:
                findings = scan_issue_for_secrets(
                    issue, patterns, jira_url,
                    email=email,
                    api_token=api_token,
                    scan_attachments=scan_attachments,
                    max_attachment_size=max_attachment_size,
                )
                if findings:
                    all_findings.extend(findings)
                    if not args.quiet:
                        print(f"   ⚠️  Secrets found in {issue.get('key')}: {len(findings)}")
            
            print()
        
        # Generate report
        print(f"\n📊 Total secrets found: {len(all_findings)}\n")
        
        # Stats for email
        scan_stats = {
            'projects_scanned': len(projects),
            'issues_scanned': total_issues_scanned,
        }
        
        report_filename = None
        
        if all_findings:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_filename = args.output or f"jira_secrets_report_{timestamp}.xlsx"
            
            if not report_filename.endswith('.xlsx'):
                report_filename += '.xlsx'
            
            print("📝 Generating report...")
            create_secrets_report(all_findings, report_filename)
            print(f"✅ Report saved: {report_filename}")
            
            # Summary stats
            print(f"\n📈 Summary:")
            print(f"   Affected projects: {len(set(f['project_key'] for f in all_findings))}")
            print(f"   Affected issues: {len(set(f['issue_key'] for f in all_findings))}")
            print(f"   Secret types: {len(set(f['secret_type'] for f in all_findings))}")
        else:
            print("✅ No secrets found.")
        
        # Send email if sender and recipient are configured
        if args.email_sender and args.email_recipient:
            print("\n📧 Sending email report...")
            
            email_config = {
                'sender': args.email_sender,
                'recipient': args.email_recipient,
                'aws_region': args.aws_region
            }
            
            # Create empty report if no secrets were found
            if not report_filename:
                report_filename = "jira_secrets_report_temp.xlsx"
                create_secrets_report(all_findings, report_filename)
            
            send_email_report(report_filename, all_findings, scan_stats, email_config)
    
    else:
        print("ℹ️  Use --scan-secrets flag to enable secret scanning.")


if __name__ == "__main__":
    main()
