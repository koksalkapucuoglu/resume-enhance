import logging
from io import BytesIO
from typing import Dict, Any, Optional
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from django.http import HttpRequest
from weasyprint import HTML, CSS
from weasyprint.text.fonts import FontConfiguration


logger = logging.getLogger(__name__)


class PdfGenerationError(Exception):
    """Custom exception for PDF generation errors."""
    pass


class HtmlToPdfConverter:
    """
    Converts HTML content to PDF using WeasyPrint.
    """
    
    def __init__(self):
        """Initialize the converter with font configuration."""
        self.font_config = FontConfiguration()
        self._setup_logging()
    
    def _setup_logging(self) -> None:
        """Configure logging for PDF generation process."""
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    def convert_html_to_pdf(self, html_content: str, css_content: Optional[str] = None) -> bytes:
        """
        Convert HTML string to PDF bytes.
        
        Args:
            html_content: The HTML content to convert
            css_content: Optional CSS content for styling
            
        Returns:
            PDF content as bytes
            
        Raises:
            PdfGenerationError: If PDF generation fails
        """
        try:
            self.logger.info("Starting HTML to PDF conversion")
            
            # Create HTML document
            html_doc = HTML(string=html_content)
            
            # Apply CSS if provided
            stylesheets = []
            if css_content:
                stylesheets.append(CSS(string=css_content))
            
            # Generate PDF
            pdf_bytes = html_doc.write_pdf(
                stylesheets=stylesheets,
                font_config=self.font_config
            )
            
            self.logger.info(f"PDF generated successfully, size: {len(pdf_bytes)} bytes")
            return pdf_bytes
            
        except Exception as e:
            error_msg = f"Failed to convert HTML to PDF: {str(e)}"
            self.logger.error(error_msg)
            raise PdfGenerationError(error_msg) from e
    
    def convert_template_to_pdf(
        self, 
        template_name: str, 
        context: Dict[str, Any], 
        request: Optional[HttpRequest] = None,
        css_file_path: Optional[Path] = None
    ) -> bytes:
        """
        Convert Django template to PDF.
        
        Args:
            template_name: Name of the Django template
            context: Template context data
            request: Optional HTTP request for context processors
            css_file_path: Optional path to CSS file for styling
            
        Returns:
            PDF content as bytes
            
        Raises:
            PdfGenerationError: If template rendering or PDF generation fails
        """
        try:
            self.logger.info(f"Converting template '{template_name}' to PDF")
            
            # Render HTML from Django template
            html_content = render_to_string(template_name, context, request)
            
            # Load CSS if provided
            css_content = None
            if css_file_path and css_file_path.exists():
                css_content = css_file_path.read_text(encoding='utf-8')
                self.logger.info(f"Loaded CSS from {css_file_path}")
            
            # Convert to PDF
            return self.convert_html_to_pdf(html_content, css_content)
            
        except Exception as e:
            error_msg = f"Failed to convert template '{template_name}' to PDF: {str(e)}"
            self.logger.error(error_msg)
            raise PdfGenerationError(error_msg) from e


class ResumePdfService:
    """
    Service for generating resume PDFs.
    
    Encapsulates resume-specific PDF generation logic.
    """
    
    def __init__(self):
        """Initialize the service with required dependencies."""
        self.pdf_converter = HtmlToPdfConverter()
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    def generate_resume_pdf(
        self, 
        resume_data: Dict[str, Any], 
        template_selector: Optional[str] = None,
        request: Optional[HttpRequest] = None
    ) -> bytes:
        """
        Generate PDF for resume data.
        
        Args:
            resume_data: Dictionary containing resume information
            template_selector: Template selector value from form (e.g., 'faangpath-simple')
            request: Optional HTTP request for context processors
            
        Returns:
            PDF content as bytes
            
        Raises:
            PdfGenerationError: If PDF generation fails
        """
        try:
            self.logger.info("Generating resume PDF")

            # Get template name from template selector
            template_html_name = settings.TEMPLATE_SELECTOR_HTML_MAP.get(template_selector)
            if not template_html_name:
                error_msg = f"Invalid template selector: {template_selector}"
                self.logger.error(error_msg)
                raise PdfGenerationError(error_msg)
            
            # Get CSS file path
            css_file_path = self._get_css_file_path()

            self.logger.info(f"Using template selector: {template_selector}")
            self.logger.info(f"Using template: {template_html_name}")
            self.logger.info(f"Using CSS file: {css_file_path if css_file_path else 'None'}")
            
            # Generate PDF
            pdf_bytes = self.pdf_converter.convert_template_to_pdf(
                template_name=template_html_name,
                context=resume_data,
                request=request,
                css_file_path=css_file_path
            )
            
            self.logger.info("Resume PDF generated successfully")
            return pdf_bytes
            
        except Exception as e:
            error_msg = f"Failed to generate resume PDF: {str(e)}"
            self.logger.error(error_msg)
            raise PdfGenerationError(error_msg) from e
    
    def _get_css_file_path(self) -> Optional[Path]:
        """
        Get CSS file path for PDF styling.
        
        Returns:
            Path to CSS file or None if not found
        """
        try:
            # Look for CSS file in templates directory
            css_file_name = "resume_pdf_styles.css"
            css_file_path = Path(settings.BASE_DIR) / "resume" / "templates" / css_file_name
            
            if css_file_path.exists():
                self.logger.info(f"Found CSS file: {css_file_path}")
                return css_file_path
            else:
                self.logger.info("No CSS file found, using default styling")
                return None
                
        except Exception as e:
            self.logger.warning(f"Error looking for CSS file: {e}")
            return None


# Service instance for dependency injection
resume_pdf_service = ResumePdfService()
