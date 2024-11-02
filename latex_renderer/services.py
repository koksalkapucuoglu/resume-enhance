import logging

from django.conf import settings

from latex_renderer import JinjaLatexHandler

logger = logging.getLogger(__name__)
logger.info(f"""
BASE_DIR: {settings.BASE_DIR}
TEMPLATE_DIR: {settings.LATEX_SETTINGS['TEMPLATE_DIR']}
TEMP_DIR: {settings.LATEX_SETTINGS['TEMP_DIR']}
""")

latex_handler = JinjaLatexHandler(settings.LATEX_SETTINGS['TEMPLATE_DIR'])
