"""
Quick test script to verify PDF service imports and basic functionality.
"""

def test_imports():
    """Test that all required imports work correctly."""
    try:
        from resume.services.pdf_service import (
            HtmlToPdfConverter, 
            ResumePdfService, 
            PdfGenerationError,
            resume_pdf_service
        )
        print("✅ PDF service imports successful")
        return True
    except ImportError as e:
        print(f"❌ Import error: {e}")
        return False

def test_service_initialization():
    """Test that services can be initialized."""
    try:
        from resume.services.pdf_service import resume_pdf_service
        print(f"✅ PDF service initialized: {type(resume_pdf_service)}")
        return True
    except Exception as e:
        print(f"❌ Service initialization error: {e}")
        return False

if __name__ == "__main__":
    print("Testing PDF Service Implementation...")
    print("=" * 50)
    
    success = True
    success &= test_imports()
    success &= test_service_initialization()
    
    if success:
        print("=" * 50)
        print("✅ All PDF service tests passed!")
    else:
        print("=" * 50)
        print("❌ Some tests failed!")
