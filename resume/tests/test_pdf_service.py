"""
Unit Tests for PDF Service

Tests following Django and Python best practices for clean, readable, and maintainable code.
"""

from pathlib import Path
from unittest.mock import Mock, patch

from django.test import RequestFactory, TestCase

from resume.services.pdf_service import (
    HtmlToPdfConverter,
    PdfGenerationError,
    ResumePdfService,
)


class HtmlToPdfConverterTestCase(TestCase):
    """Test cases for HtmlToPdfConverter following clean testing principles."""

    def setUp(self):
        """Set up test fixtures with meaningful names."""
        self.converter = HtmlToPdfConverter()
        self.sample_html = "<html><body><h1>Test Resume</h1></body></html>"
        self.sample_css = "body { font-family: Arial; }"

    @patch("resume.services.pdf_service.settings")
    @patch("resume.services.pdf_service.HTML")
    def test_convert_html_to_pdf_success(self, mock_html_class, mock_settings):
        """Test successful HTML to PDF conversion."""
        mock_settings.BASE_DIR = "/app"
        mock_html_instance = Mock()
        mock_html_class.return_value = mock_html_instance
        expected_pdf_bytes = b"fake pdf content"
        mock_html_instance.write_pdf.return_value = expected_pdf_bytes

        result = self.converter.convert_html_to_pdf(self.sample_html)

        self.assertEqual(result, expected_pdf_bytes)
        mock_html_class.assert_called_once_with(
            string=self.sample_html, base_url="/app"
        )
        mock_html_instance.write_pdf.assert_called_once()

    @patch("resume.services.pdf_service.settings")
    @patch("resume.services.pdf_service.HTML")
    def test_convert_html_to_pdf_with_css(self, mock_html_class, mock_settings):
        """Test HTML to PDF conversion with CSS styling."""
        mock_settings.BASE_DIR = "/app"
        mock_html_instance = Mock()
        mock_html_class.return_value = mock_html_instance
        expected_pdf_bytes = b"styled pdf content"
        mock_html_instance.write_pdf.return_value = expected_pdf_bytes

        result = self.converter.convert_html_to_pdf(self.sample_html, self.sample_css)

        self.assertEqual(result, expected_pdf_bytes)
        call_args = mock_html_instance.write_pdf.call_args
        self.assertIn("stylesheets", call_args.kwargs)

    @patch("resume.services.pdf_service.settings")
    @patch("resume.services.pdf_service.HTML")
    def test_convert_html_to_pdf_failure(self, mock_html_class, mock_settings):
        """Test PDF conversion failure handling."""
        mock_settings.BASE_DIR = "/app"
        mock_html_class.side_effect = Exception("WeasyPrint error")

        with self.assertRaises(PdfGenerationError) as context:
            self.converter.convert_html_to_pdf(self.sample_html)

        self.assertIn("Failed to convert HTML to PDF", str(context.exception))

    @patch("resume.services.pdf_service.render_to_string")
    def test_convert_template_to_pdf_success(self, mock_render_to_string):
        """Test successful template to PDF conversion."""
        template_name = "test_template.html"
        context = {"name": "John Doe"}
        mock_render_to_string.return_value = self.sample_html

        with patch.object(self.converter, "convert_html_to_pdf") as mock_convert:
            expected_pdf = b"template pdf content"
            mock_convert.return_value = expected_pdf

            result = self.converter.convert_template_to_pdf(template_name, context)

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
            "user_data": {
                "full_name": "John Doe",
                "email": "john@example.com",
                "phone": "+1234567890",
            },
            "education_data": [],
            "experience_data": [],
            "project_data": [],
        }

    def test_initialization_creates_pdf_converter(self):
        """Test service initialization creates required dependencies."""
        self.assertIsInstance(self.service.pdf_converter, HtmlToPdfConverter)
        self.assertIsNotNone(self.service.logger)

    @patch("resume.services.pdf_service.settings")
    def test_generate_resume_pdf_invalid_template(self, mock_settings):
        """Test that an unknown template selector raises PdfGenerationError."""
        mock_settings.TEMPLATE_SELECTOR_HTML_MAP = {}

        with self.assertRaises(PdfGenerationError) as context:
            self.service.generate_resume_pdf(
                self.sample_resume_data, "nonexistent-template"
            )

        self.assertIn("Invalid template selector", str(context.exception))

    @patch("resume.services.pdf_service.settings")
    def test_get_css_file_path_exists(self, mock_settings):
        """Test CSS file path retrieval when file exists."""
        mock_settings.BASE_DIR = "/app"

        with patch.object(Path, "exists", return_value=True):
            result = self.service._get_css_file_path()

        self.assertIsNotNone(result)
        self.assertIsInstance(result, Path)

    @patch("resume.services.pdf_service.settings")
    def test_get_css_file_path_not_exists(self, mock_settings):
        """Test CSS file path retrieval when file doesn't exist."""
        mock_settings.BASE_DIR = "/app"

        with patch.object(Path, "exists", return_value=False):
            result = self.service._get_css_file_path()

        self.assertIsNone(result)

    @patch("resume.services.pdf_service.settings")
    def test_generate_resume_pdf_success(self, mock_settings):
        """Test successful resume PDF generation."""
        mock_settings.TEMPLATE_SELECTOR_HTML_MAP = {
            "faangpath-simple": "test_template.html"
        }
        mock_settings.BASE_DIR = "/app"
        request = self.request_factory.get("/")

        with patch.object(self.service, "_get_css_file_path", return_value=None):
            with patch.object(
                self.service.pdf_converter, "convert_template_to_pdf"
            ) as mock_convert:
                expected_pdf = b"resume pdf content"
                mock_convert.return_value = expected_pdf

                result = self.service.generate_resume_pdf(
                    self.sample_resume_data, "faangpath-simple", request
                )

                self.assertEqual(result, expected_pdf)
                mock_convert.assert_called_once_with(
                    template_name="test_template.html",
                    context=self.sample_resume_data,
                    request=request,
                    css_file_path=None,
                )

    @patch("resume.services.pdf_service.settings")
    def test_generate_resume_pdf_failure(self, mock_settings):
        """Test resume PDF generation failure handling."""
        mock_settings.TEMPLATE_SELECTOR_HTML_MAP = {
            "faangpath-simple": "test_template.html"
        }
        mock_settings.BASE_DIR = "/app"

        with patch.object(self.service, "_get_css_file_path", return_value=None):
            with patch.object(
                self.service.pdf_converter, "convert_template_to_pdf"
            ) as mock_convert:
                mock_convert.side_effect = Exception("Template error")

                with self.assertRaises(PdfGenerationError) as context:
                    self.service.generate_resume_pdf(
                        self.sample_resume_data, "faangpath-simple"
                    )

                self.assertIn("Failed to generate resume PDF", str(context.exception))


class PdfServiceIntegrationTestCase(TestCase):
    """Integration tests for PDF service with real components."""

    def setUp(self):
        """Set up integration test fixtures."""
        self.service = ResumePdfService()
        self.sample_context = {
            "user_data": {
                "full_name": "Integration Test User",
                "email": "test@integration.com",
            }
        }

    def test_service_instance_availability(self):
        """Test that service instance is properly configured."""
        from resume.services.pdf_service import resume_pdf_service

        self.assertIsNotNone(resume_pdf_service)
        self.assertIsInstance(resume_pdf_service, ResumePdfService)

    @patch("resume.services.pdf_service.settings")
    @patch("resume.services.pdf_service.render_to_string")
    @patch("resume.services.pdf_service.HTML")
    def test_end_to_end_pdf_generation(
        self, mock_html_class, mock_render, mock_settings
    ):
        """Test end-to-end PDF generation workflow."""
        mock_settings.TEMPLATE_SELECTOR_HTML_MAP = {"faangpath-simple": "test.html"}
        mock_settings.BASE_DIR = "/app"
        mock_render.return_value = "<html><body>Test</body></html>"
        mock_html_instance = Mock()
        mock_html_class.return_value = mock_html_instance
        mock_html_instance.write_pdf.return_value = b"integration test pdf"

        with patch.object(Path, "exists", return_value=False):
            result = self.service.generate_resume_pdf(
                self.sample_context, "faangpath-simple"
            )

        self.assertEqual(result, b"integration test pdf")
        mock_render.assert_called_once()
        mock_html_instance.write_pdf.assert_called_once()
