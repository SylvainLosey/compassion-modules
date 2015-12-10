# -*- encoding: utf-8 -*-
##############################################################################
#
#    Copyright (C) 2015 Compassion CH (http://www.compassion.ch)
#    Releasing children from poverty in Jesus' name
#    @author: Roman Zoller
#
#    The licence is in the file __openerp__.py
#
##############################################################################

from .. import tools
from openerp.tests import common

import base64
import os
import re

IMPORT_DIR = os.path.join(os.path.dirname(__file__), "testdata/import")


def get_file_content(path):
    """ Reads a file and returns a base64 encoding of its contents. """
    with open(path, "rb") as f:
        file_content = f.read()
        return base64.b64encode(file_content)


def import_letters(letters_obj, filename):
    """ Reads and imports letters into the database. """
    path = os.path.join(IMPORT_DIR, filename)
    file_content = get_file_content(path)
    attachment = {
        "name": filename,
        "db_datas": file_content,
    }
    letters = letters_obj.create({"data": [(0, 0, attachment)]})
    letters.button_import()
    return letters


def get_template_list(data_obj):
    """
    Returns a dict of all templates with externals ids as keys and ids as
    corresponding values.
    """
    data = data_obj.search(
        [("model", "=", "sponsorship.correspondence.template")])
    template_list = {}
    for d in data:
        template_list[d.name] = d.res_id
    return template_list


def generate_test(dataset):
    """ Generates a test for the import of one letter. """
    def test_import(self):
        imports = import_letters(self.env["import.letters.history"],
                                 dataset["name"])
        template_list = get_template_list(self.env["ir.model.data"])
        problems = filter(None, dataset["problems"].split(","))
        if problems:
            self.assertEqual(imports.state, "open")
        else:
            self.assertEqual(imports.state, "ready")
        self.assertEqual(imports.nber_letters, 1)
        self.assertFalse(imports.is_mandatory_review)
        letters = imports.import_line_ids
        self.assertEqual(len(letters), 1)
        letter = letters[0]
        if "partner" not in problems:
            self.assertEqual(letter.partner_id.ref, dataset["partner"])
        if "child" not in problems:
            self.assertEqual(letter.child_id.code, dataset["child"])
        template_id = template_list[dataset["template_id"]]
        self.assertEqual(letter.template_id.id, template_id)
        if "lang" not in problems:
            self.assertEqual(letter.letter_language_id.code_iso,
                             dataset["lang"])
    return test_import


class TestLetterImportGenerator(type):
    """
    Generates a test function for each entry of a list of files.
    """
    def __new__(mcs, name, bases, dict):
        file_index = os.path.join(IMPORT_DIR, "travis_files.csv")
        with open(file_index, 'rb') as csv_file:
            file_list = tools.import_letter_functions.read_csv(csv_file)
            for f in file_list:
                sanitized_filename = re.sub(r"[^a-zA-Z0-9]", "_", f["name"])
                test_name = "test_import_{}".format(sanitized_filename)
                dict[test_name] = generate_test(f)
        return type.__new__(mcs, name, bases, dict)


class TestLetterImport(common.TransactionCase):
    """
    Tests import of letters and verifies correct parsing. Letters are given as
    .tif files; a list of these files is read from a .csv file, along with
    expected parsing results.
    """
    __metaclass__ = TestLetterImportGenerator
