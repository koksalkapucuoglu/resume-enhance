import subprocess
import pathlib
import logging

class TexToPdfConverter:
    def __init__(self, tex_file_path: pathlib.Path):
        """
        Initialize PdfLatex with generated tex file path.

        Args:
            tex_file_path (pathlib.Path): Path to the .tex file.
        """

        self.tex_file_path = tex_file_path
        self.output_directory = tex_file_path.parent
        self.pdf_file_path = tex_file_path.with_suffix(".pdf")

    def render_pdf(self) -> pathlib.Path:
        """
        Converts a .tex file to PDF and cleans up temporary files.

        Returns:
            pathlib.Path: Path to the generated PDF file.
        """

        self._check_tex_file_exists()

        self._remove_existing_pdf()

        self._run_pdflatex()

        self._check_pdf_creation()

        self._cleanup()

        return self.pdf_file_path

    def _check_tex_file_exists(self):
        """Check if the .tex file exists."""
        if not self.tex_file_path.is_file():
            raise FileNotFoundError(
                f"The file {self.tex_file_path} doesn't exist!")

    def _remove_existing_pdf(self):
        """Remove existing PDF file if it exists."""
        if self.pdf_file_path.is_file():
            self.pdf_file_path.unlink()

    def _run_pdflatex(self):
        """Run the pdflatex command to convert .tex to .pdf."""
        command = [
            "pdflatex",
            "-interaction=batchmode",
            "-output-directory", str(self.output_directory),
            "-quiet",
            str(self.tex_file_path)
        ]

        logging.info(f"Running pdflatex for: {self.tex_file_path}")
        result = subprocess.run(
            command,
            check=True,
            cwd=self.output_directory,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to render PDF: {result.stderr.decode()}")

    def _check_pdf_creation(self):
        """Ensure the PDF file was successfully created."""
        if not self.pdf_file_path.is_file():
            raise RuntimeError(
                f"PDF file was not created as expected: {self.pdf_file_path}")

    def _cleanup(self):
        """Clean up non-PDF files in the output directory."""
        for file in self.output_directory.iterdir():
            if file.stem == self.tex_file_path.stem and file.suffix != ".pdf":
                try:
                    logging.info(f"Cleaning up: {file}")
                    file.unlink()
                except Exception as e:
                    logging.warning(f"Failed to delete {file}: {e}")

    def __str__(self) -> str:
        """Return a string representation of the TexToPdfConverter instance."""
        return (f"TexToPdfConverter(\n"
                f"  tex_file_path={self.tex_file_path},\n"
                f"  output_directory={self.output_directory},\n"
                f"  pdf_file_path={self.pdf_file_path}\n"
                f")")
