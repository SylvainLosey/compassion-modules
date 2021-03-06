# -*- coding: utf-8 -*-
##############################################################################
#
#    Copyright (C) 2017 Compassion CH (http://www.compassion.ch)
#    Releasing children from poverty in Jesus' name
#
#    The licence is in the file __manifest__.py
#
##############################################################################
#
#   TODO: this file can be deleted once the migration 10.0.1.6.0 has terminated
#
#   The migration to v10.0.1.6.0 extracts texts and images from the pdf and stores them
#   in the correspondence objects. The original PDFs were storing the large templates
#   backgrounds and we want to save disk space by dropping them.
#
#   Going through all the correspondences during the migration would take way too much
#   time (PDF reads, image extractions, database writes...) blocking the production
#   so we split the migration in batches of 10 correspondences.
#
###############################################################################
from odoo import api, models
from odoo.addons.queue_job.job import job
from io import BytesIO
import logging
import base64

_logger = logging.getLogger(__name__)

try:
    import PyPDF2
    from PIL import Image

    from pdfminer.converter import PDFPageAggregator
    from pdfminer.layout import LAParams, LTTextBox, LTTextLine
    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
    from pdfminer.pdfpage import PDFPage
    from pdfminer.pdfparser import PDFParser

except ImportError:
    _logger.error("Python libraries 'PyPDF2', 'pdfminer' and 'Pillow' are required for"
                  " the correspondences migration in order to extract images from PDFs")


class CorrespondenceMigration(models.AbstractModel):
    _name = 'correspondence.migration'

    @job(default_channel='root.sbc_compassion_migration')
    @api.model
    def migrate(self, correspondences_ids):

        templates_backgrounds_sizes = get_template_background_sizes(self.env)
        correspondences = self.env['correspondence'].browse(correspondences_ids)

        _logger.info("Starting migration of {} correspondences".format(
            len(correspondences)))

        migrated = self.env['correspondence']
        for c in correspondences:
            try:
                if not c.letter_image:
                    continue

                # Read the PDF in memory
                pdf_data = BytesIO(base64.b64decode(c.letter_image))

                # Retrieve the text. If we fail, we don't want to delete the PDF.
                original_text = extract_text_from_pdf(pdf_data)
                if not original_text or not len(original_text):
                    continue

                # Retrieve optional sponsor's images.
                attachments = extract_attachment_from_pdf(pdf_data,
                                                          templates_backgrounds_sizes)

                # Retrieve the text in 'original_text'. It is usually not the original
                # text but the translated one sent to GMC. We move it to 'english_text'
                english_text = c.original_text
                c.page_ids.unlink()
                c.original_attachment_ids.unlink()

                # Write the modififications
                attachment_vals = []
                for filename in attachments:
                    buffered = BytesIO()
                    attachments[filename].save(buffered, format="JPEG")
                    attachment_vals.append((0, 0, {
                        'datas_fname': filename,
                        'datas': base64.b64encode(buffered.getvalue()),
                        'name': filename,
                        'res_model': c._name,
                    }))

                c.write({
                    'letter_image': False,
                    'store_letter_image': False,
                    'original_attachment_ids': attachment_vals,
                    'original_text': original_text,
                    'english_text': english_text
                })
                migrated += c
            except:
                _logger.error(
                    "Correspondence migration (id={}): Failed".format(c.id),
                    exc_info=True)

        _logger.info("successfully migrated {}/{} correspondences".format(
            len(migrated), len(correspondences)))
        _logger.warn(correspondences - migrated)
        return True


def get_template_background_sizes(env):
    """
    List the sizes of all templates' backgrounds
    """
    templates = env['correspondence.template'].search([])
    sizes = set()
    for template in templates:
        for page in template.page_ids:
            if page.background:
                img = Image.open(BytesIO(base64.b64decode(page.background)))
                sizes.add((img.width, img.height))
    return sizes


def extract_text_from_pdf(pdf_data):
    """
    Extract and concatenate all text boxes from a PDF (BytesIO stream)

    Along the years, PDFs have been generated by different methods and templates:
        - Headers *might* be present on all pages, are not always in the same location
          and have different text boxes and content along the versions
        - Body texts are sometimes *justified* in the text boxes by using line breaks at
          the end of every lines
        - A single paragraph (visually) might be broken into multiple boxes and multiple
          paragraphs might be stored together in a single box
        - The order of the boxes/texts is not conserved in the PDF
    """
    parser = PDFParser(pdf_data)
    document = PDFDocument(parser)
    resmgr = PDFResourceManager()
    laparams = LAParams()
    device = PDFPageAggregator(resmgr, laparams=laparams)
    interpreter = PDFPageInterpreter(resmgr, device)

    headers = ""
    texts = []
    page_index = 0
    for page in PDFPage.create_pages(document):
        interpreter.process_page(page)
        for obj in device.get_result():
            if isinstance(obj, (LTTextBox, LTTextLine)):
                text = obj.get_text().replace('\n', ' ')
                if page_index == 0:
                    if obj.y0 > device.result.height * 0.9:
                        headers = obj._objs
                    else:
                        texts.append((page_index, -obj.y0, text))
                else:
                    if obj._objs[0].get_text() != headers[0].get_text():
                        texts.append((page_index, -obj.y0, text))
        page_index += 1

    # Sort the texts by (page, position)
    texts = [t[2] for t in sorted(texts)]
    return '\n'.join(texts)


def extract_attachment_from_pdf(pdf_data, size_filter=None):
    """
    Extract images from a PDF (BytesIO stream)

    We don't want to export backgrounds (which are saved in templates) to save disk
    space. We use the images dimensions to detect and filter those.
    """
    images = {}
    encoders = {
        '/DCTDecode': '.jpg',
        '/JPXDecode': '.jp2',
        '/CCITTFaxDecode': '.tiff',
        # '/FlatDecode': '.png'
    }
    if size_filter is None:
        size_filter = []

    pdf = PyPDF2.PdfFileReader(pdf_data)
    x_object = pdf.getPage(0)['/Resources']['/XObject'].getObject()

    for obj in x_object:
        if x_object[obj]['/Subtype'] == '/Image':
            size = (x_object[obj]['/Width'], x_object[obj]['/Height'])
            if size in size_filter:
                continue

            data = x_object[obj].getData()
            if '/Filter' in x_object[obj]:
                _filter = x_object[obj]['/Filter']
                if _filter in encoders:
                    img = Image.open(BytesIO(data))
                    filename = obj[1:] + encoders[_filter]
                else:
                    mode = x_object[obj]['/ColorSpace'] == '/DeviceRGB' and "RGB" or "P"
                    img = Image.frombytes(mode, size, data)
                    filename = obj[1:] + '.png'
                images[filename] = img
    return images
