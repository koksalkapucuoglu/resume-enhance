import pathlib
from typing import Dict
from datetime import datetime

from jinja2 import Environment, FileSystemLoader, select_autoescape


class JinjaLatexHandler:
    def __init__(self, template_dir: pathlib.Path):
        """
        Initialize Jinja2 environment with LaTeX-specific configurations.

        Args:
            template_dir: Directory containing LaTeX templates
        """
        self.env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=select_autoescape(['tex']),
            block_start_string='<%',
            block_end_string='%>',
            variable_start_string='<<',
            variable_end_string='>>',
            comment_start_string='<#',
            comment_end_string='#>',
            trim_blocks=True,
            lstrip_blocks=True
        )

        self.env.filters['tex_escape'] = self.tex_escape
        self.env.filters['date_format'] = self.date_format

    @staticmethod
    def tex_escape(text: str) -> str:
        """
        Make escape for LaTeX special characters.
        """
        conv = {
            '&': r'\&',
            '%': r'\%',
            '$': r'\$',
            '#': r'\#',
            '_': r'\_',
            '{': r'\{',
            '}': r'\}',
            '~': r'\textasciitilde{}',
            '^': r'\^{}',
            '\\': r'\textbackslash{}',
            '<': r'\textless{}',
            '>': r'\textgreater{}',
        }
        return ''.join(conv.get(c, c) for c in str(text))

    @staticmethod
    def date_format(date_string, format_str="%B %Y"):
        """
        Format a date string from "YYYY-MM-DD" to desired format.
        Default format is "Month Year" (e.g., April 2025)
        """
        if not date_string:
            return ""
        try:
            date_obj = datetime.strptime(str(date_string), "%Y-%m-%d")
            return date_obj.strftime(format_str)
        except ValueError:
            return date_string

    def render_tex_template(self, template_name: str, context: Dict,
                      output_path: pathlib.Path) -> pathlib.Path:
        """
        Renders the LaTeX template with provided data.

        Args:
            template_name (str): The .tex file name within the template directory.
            context (Dict): Data to be passed into the template.
            output_path (pathlib.Path): Path to save the rendered .tex file.

        Returns:
            pathlib.Path: Path to the rendered .tex file.
        """

        # JinjaLatexHandler search the file in template_dir(which given at initialization)
        template = self.env.get_template(template_name)

        rendered_content = template.render(**context)

        # Ensure the output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Save the rendered LaTeX content to the specified output path
        with output_path.open('w', encoding='utf-8') as f:
            f.write(rendered_content)

        return output_path