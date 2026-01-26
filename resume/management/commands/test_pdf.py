"""
Django management command to test PDF generation functionality.

This command provides a clean way to test the PDF service without going through the web interface.
Follows Django best practices for management commands.
"""

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from pathlib import Path

from resume.services.pdf_service import resume_pdf_service, PdfGenerationError


class Command(BaseCommand):
    """
    Management command for testing PDF generation.
    
    Provides meaningful help text and clean error handling.
    """
    
    help = 'Test PDF generation functionality with sample resume data'
    
    def add_arguments(self, parser):
        """Add command line arguments with clear descriptions."""
        parser.add_argument(
            '--output-file',
            type=str,
            default='test_resume.pdf',
            help='Output filename for the generated PDF (default: test_resume.pdf)'
        )
        
        parser.add_argument(
            '--output-dir',
            type=str,
            default='.',
            help='Output directory for the generated PDF (default: current directory)'
        )
        
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Enable verbose output for debugging'
        )
    
    def handle(self, *args, **options):
        """
        Execute the command with clean error handling and meaningful output.
        
        Args:
            *args: Positional arguments
            **options: Command options from argument parser
        """
        try:
            # Setup output configuration
            output_filename = options['output_file']
            output_dir = Path(options['output_dir']).resolve()
            output_path = output_dir / output_filename
            verbose = options['verbose']
            
            if verbose:
                self.stdout.write(f"Output directory: {output_dir}")
                self.stdout.write(f"Output file: {output_filename}")
            
            # Create sample resume data
            sample_data = self._create_sample_resume_data()
            
            if verbose:
                self.stdout.write("Generated sample resume data")
            
            # Generate PDF
            self.stdout.write("Generating PDF...")
            pdf_bytes = resume_pdf_service.generate_resume_pdf(sample_data)
            
            # Save PDF to file
            self._save_pdf_to_file(pdf_bytes, output_path)
            
            # Success message
            self.stdout.write(
                self.style.SUCCESS(
                    f'Successfully generated PDF: {output_path}'
                )
            )
            
            if verbose:
                self.stdout.write(f"PDF size: {len(pdf_bytes)} bytes")
                
        except PdfGenerationError as e:
            raise CommandError(f'PDF generation failed: {e}')
            
        except FileNotFoundError as e:
            raise CommandError(f'Output directory not found: {e}')
            
        except PermissionError as e:
            raise CommandError(f'Permission denied writing to output file: {e}')
            
        except Exception as e:
            raise CommandError(f'Unexpected error: {e}')
    
    def _create_sample_resume_data(self) -> dict:
        """
        Create comprehensive sample resume data for testing.
        
        Returns:
            Dictionary containing sample resume information
        """
        return {
            'user_data': {
                'full_name': 'John Software Engineer',
                'email': 'john.engineer@example.com',
                'phone': '+1 (555) 123-4567',
                'linkedin': 'linkedin.com/in/john-engineer',
                'github': 'github.com/john-engineer',
                'skills': 'Python, Django, JavaScript, React, PostgreSQL, Docker, AWS, Git, REST APIs, Test-Driven Development'
            },
            'education_data': [
                {
                    'degree': 'Bachelor',
                    'field_of_study': 'Computer Science',
                    'school': 'University of Technology',
                    'start_date': '2018-09-01',
                    'end_date': '2022-05-15'
                },
                {
                    'degree': 'Master',
                    'field_of_study': 'Software Engineering',
                    'school': 'Institute of Advanced Computing',
                    'start_date': '2022-09-01',
                    'end_date': '2024-05-15'
                }
            ],
            'experience_data': [
                {
                    'title': 'Senior Software Engineer',
                    'company': 'Tech Innovations Inc.',
                    'start_date': '2024-06-01',
                    'end_date': '2025-06-27',
                    'current_role': True,
                    'description': [
                        'Led development of microservices architecture serving 1M+ daily active users using Python/Django and Docker',
                        'Improved application performance by 40% through database optimization and caching strategies',
                        'Mentored 3 junior developers and established code review processes that reduced bugs by 25%',
                        'Implemented CI/CD pipelines using GitHub Actions, reducing deployment time from 2 hours to 15 minutes'
                    ]
                },
                {
                    'title': 'Software Engineer',
                    'company': 'StartupXYZ',
                    'start_date': '2022-08-01',
                    'end_date': '2024-05-31',
                    'current_role': False,
                    'description': [
                        'Developed RESTful APIs using Django REST Framework for mobile and web applications',
                        'Built responsive front-end interfaces with React and modern JavaScript (ES6+)',
                        'Collaborated with cross-functional teams in Agile environment using Scrum methodology',
                        'Maintained 95%+ test coverage through unit and integration testing with pytest'
                    ]
                }
            ],
            'project_data': [
                {
                    'name': 'AI-Powered Resume Builder',
                    'description': 'Full-stack web application using Django, React, and OpenAI API that helps users create professional resumes with AI-powered content enhancement. Deployed on AWS with PostgreSQL database.',
                    'link': 'https://github.com/john-engineer/resume-builder'
                },
                {
                    'name': 'E-commerce Analytics Dashboard',
                    'description': 'Real-time analytics dashboard built with Python, Django, and D3.js for tracking e-commerce metrics. Processes 100K+ transactions daily and provides actionable business insights.',
                    'link': 'https://github.com/john-engineer/ecommerce-analytics'
                },
                {
                    'name': 'Open Source Contribution: Django Performance Monitor',
                    'description': 'Contributed performance monitoring middleware to popular Django package with 10K+ downloads. Implemented request timing, memory usage tracking, and database query optimization.',
                    'link': 'https://github.com/django-community/performance-monitor'
                }
            ],
            'generation_date': '2025-06-27'
        }
    
    def _save_pdf_to_file(self, pdf_bytes: bytes, output_path: Path) -> None:
        """
        Save PDF bytes to file with proper error handling.
        
        Args:
            pdf_bytes: PDF content as bytes
            output_path: Path where to save the PDF file
            
        Raises:
            FileNotFoundError: If output directory doesn't exist
            PermissionError: If cannot write to output file
        """
        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write PDF content to file
        with open(output_path, 'wb') as pdf_file:
            pdf_file.write(pdf_bytes)
