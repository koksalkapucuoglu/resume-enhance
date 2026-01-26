"""
Unit Tests for PDF Service

Tests following Django and Python best practices for clean, readable, and maintainable code.
"""

from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

from django.test import TestCase, RequestFactory
from django.conf import settings

from resume.services.pdf_service import (
    HtmlToPdfConverter, 
    ResumePdfService, 
    PdfGenerationError
)


class HtmlToPdfConverterTestCase(TestCase):
    """Test cases for HtmlToPdfConverter following clean testing principles."""
    
    def setUp(self):
        """Set up test fixtures with meaningful names."""
        self.converter = HtmlToPdfConverter()
        self.sample_html = "<html><body><h1>Test Resume</h1></body></html>"
        self.sample_css = "body { font-family: Arial; }"
    
    @patch('resume.services.pdf_service.HTML')
    def test_convert_html_to_pdf_success(self, mock_html_class):
        """Test successful HTML to PDF conversion."""
        # Arrange
        mock_html_instance = Mock()
        mock_html_class.return_value = mock_html_instance
        expected_pdf_bytes = b"fake pdf content"
        mock_html_instance.write_pdf.return_value = expected_pdf_bytes
        
        # Act
        result = self.converter.convert_html_to_pdf(self.sample_html)
        
        # Assert
        self.assertEqual(result, expected_pdf_bytes)
        mock_html_class.assert_called_once_with(string=self.sample_html)
        mock_html_instance.write_pdf.assert_called_once()
    
    @patch('resume.services.pdf_service.HTML')
    def test_convert_html_to_pdf_with_css(self, mock_html_class):
        """Test HTML to PDF conversion with CSS styling."""
        # Arrange
        mock_html_instance = Mock()
        mock_html_class.return_value = mock_html_instance
        expected_pdf_bytes = b"styled pdf content"
        mock_html_instance.write_pdf.return_value = expected_pdf_bytes
        
        # Act
        result = self.converter.convert_html_to_pdf(self.sample_html, self.sample_css)
        
        # Assert
        self.assertEqual(result, expected_pdf_bytes)
        # Verify CSS was applied
        call_args = mock_html_instance.write_pdf.call_args
        self.assertIn('stylesheets', call_args.kwargs)
    
    @patch('resume.services.pdf_service.HTML')
    def test_convert_html_to_pdf_failure(self, mock_html_class):
        """Test PDF conversion failure handling."""
        # Arrange
        mock_html_class.side_effect = Exception("WeasyPrint error")
        
        # Act & Assert
        with self.assertRaises(PdfGenerationError) as context:
            self.converter.convert_html_to_pdf(self.sample_html)
        
        self.assertIn("Failed to convert HTML to PDF", str(context.exception))
    
    @patch('resume.services.pdf_service.render_to_string')
    def test_convert_template_to_pdf_success(self, mock_render_to_string):
        """Test successful template to PDF conversion."""
        # Arrange
        template_name = "test_template.html"
        context = {"name": "John Doe"}
        mock_render_to_string.return_value = self.sample_html
        
        with patch.object(self.converter, 'convert_html_to_pdf') as mock_convert:
            expected_pdf = b"template pdf content"
            mock_convert.return_value = expected_pdf
            
            # Act
            result = self.converter.convert_template_to_pdf(template_name, context)
            
            # Assert
            self.assertEqual(result, expected_pdf)
            mock_render_to_string.assert_called_once_with(template_name, context, None)
            mock_convert.assert_called_once_with(self.sample_html, None)


class ResumePdfServiceTestCase(TestCase):
    """Test cases for ResumePdfService with meaningful test scenarios."""
    
    def setUp(self):
        """Set up test fixtures and dependencies."""
        self.service = ResumePdfService()
        self.request_factory = RequestFactory()
        self.sample_resume_data = {
            'user_data': {
                'full_name': 'John Doe',
                'email': 'john@example.com',
                'phone': '+1234567890'
            },
            'education_data': [],
            'experience_data': [],
            'project_data': []
        }
    
    def test_initialization_creates_pdf_converter(self):
        """Test service initialization creates required dependencies."""
        # Assert
        self.assertIsInstance(self.service.pdf_converter, HtmlToPdfConverter)
        self.assertIsNotNone(self.service.logger)
    
    @patch('resume.services.pdf_service.settings')
    def test_get_html_template_name_success(self, mock_settings):
        """Test successful template name retrieval."""
        # Arrange
        mock_settings.LATEX_SETTINGS = {"DEFAULT_TEMPLATE": "test.tex"}
        mock_settings.TEX_PREVIEW_HTML_MAP = {"test.tex": "test.html"}
        
        # Act
        result = self.service._get_html_template_name()
        
        # Assert
        self.assertEqual(result, "test.html")
    
    @patch('resume.services.pdf_service.settings')
    def test_get_html_template_name_failure(self, mock_settings):
        """Test template name retrieval failure."""
        # Arrange
        mock_settings.LATEX_SETTINGS = {"DEFAULT_TEMPLATE": "missing.tex"}
        mock_settings.TEX_PREVIEW_HTML_MAP = {}
        
        # Act & Assert
        with self.assertRaises(PdfGenerationError) as context:
            self.service._get_html_template_name()
        
        self.assertIn("No HTML template mapping found", str(context.exception))
    
    @patch('resume.services.pdf_service.Path')
    @patch('resume.services.pdf_service.settings')
    def test_get_css_file_path_exists(self, mock_settings, mock_path_class):
        """Test CSS file path retrieval when file exists."""
        # Arrange
        mock_settings.BASE_DIR = "/app"
        mock_css_path = Mock()
        mock_css_path.exists.return_value = True
        mock_path_class.return_value = mock_css_path
        
        # Act
        result = self.service._get_css_file_path()
        
        # Assert
        self.assertEqual(result, mock_css_path)
    
    @patch('resume.services.pdf_service.Path')
    @patch('resume.services.pdf_service.settings')
    def test_get_css_file_path_not_exists(self, mock_settings, mock_path_class):
        """Test CSS file path retrieval when file doesn't exist."""
        # Arrange
        mock_settings.BASE_DIR = "/app"
        mock_css_path = Mock()
        mock_css_path.exists.return_value = False
        mock_path_class.return_value = mock_css_path
        
        # Act
        result = self.service._get_css_file_path()
        
        # Assert
        self.assertIsNone(result)
    
    @patch.object(ResumePdfService, '_get_css_file_path')
    @patch.object(ResumePdfService, '_get_html_template_name')
    def test_generate_resume_pdf_success(self, mock_get_template, mock_get_css):
        """Test successful resume PDF generation."""
        # Arrange
        mock_get_template.return_value = "test_template.html"
        mock_get_css.return_value = None
        request = self.request_factory.get('/')
        
        with patch.object(self.service.pdf_converter, 'convert_template_to_pdf') as mock_convert:
            expected_pdf = b"resume pdf content"
            mock_convert.return_value = expected_pdf
            
            # Act
            result = self.service.generate_resume_pdf(self.sample_resume_data, request)
            
            # Assert
            self.assertEqual(result, expected_pdf)
            mock_convert.assert_called_once_with(
                template_name="test_template.html",
                context=self.sample_resume_data,
                request=request,
                css_file_path=None
            )
    
    @patch.object(ResumePdfService, '_get_html_template_name')
    def test_generate_resume_pdf_failure(self, mock_get_template):
        """Test resume PDF generation failure handling."""
        # Arrange
        mock_get_template.side_effect = Exception("Template error")
        
        # Act & Assert
        with self.assertRaises(PdfGenerationError) as context:
            self.service.generate_resume_pdf(self.sample_resume_data)
        
        self.assertIn("Failed to generate resume PDF", str(context.exception))


class PdfServiceIntegrationTestCase(TestCase):
    """Integration tests for PDF service with real components."""
    
    def setUp(self):
        """Set up integration test fixtures."""
        self.service = ResumePdfService()
        self.sample_context = {
            'user_data': {
                'full_name': 'Integration Test User',
                'email': 'test@integration.com'
            }
        }
    
    def test_service_instance_availability(self):
        """Test that service instance is properly configured."""
        from resume.services.pdf_service import resume_pdf_service
        
        # Assert
        self.assertIsNotNone(resume_pdf_service)
        self.assertIsInstance(resume_pdf_service, ResumePdfService)
    
    @patch('resume.services.pdf_service.render_to_string')
    @patch('resume.services.pdf_service.HTML')
    def test_end_to_end_pdf_generation(self, mock_html_class, mock_render):
        """Test end-to-end PDF generation workflow."""
        # Arrange
        mock_render.return_value = "<html><body>Test</body></html>"
        mock_html_instance = Mock()
        mock_html_class.return_value = mock_html_instance
        mock_html_instance.write_pdf.return_value = b"integration test pdf"
        
        # Mock settings
        with patch('resume.services.pdf_service.settings') as mock_settings:
            mock_settings.LATEX_SETTINGS = {"DEFAULT_TEMPLATE": "test.tex"}
            mock_settings.TEX_PREVIEW_HTML_MAP = {"test.tex": "test.html"}
            mock_settings.BASE_DIR = "/app"
            
            # Act
            result = self.service.generate_resume_pdf(self.sample_context)
            
            # Assert
            self.assertEqual(result, b"integration test pdf")
            mock_render.assert_called_once()
            mock_html_instance.write_pdf.assert_called_once()
