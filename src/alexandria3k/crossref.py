#
# Alexandria3k Crossref bibliographic metadata processing
# Copyright (C) 2022  Diomidis Spinellis
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
"""Virtual database table access of Crossref data"""

import abc
import os

import apsw

from file_cache import get_file_cache
from virtual_db import (
    ColumnMeta,
    TableMeta,
    CONTAINER_ID_COLUMN,
    FilesCursor,
    ROWID_INDEX,
)


class DataFiles:
    """The source of the compressed JSON data files"""

    def __init__(self, directory, sample_container=lambda path: True):
        # Collect the names of all available data files
        self.data_files = []
        counter = 1
        for file_name in os.listdir(directory):
            path = os.path.join(directory, file_name)
            if not os.path.isfile(path):
                continue
            if not sample_container(path):
                continue
            counter += 1
            self.data_files.append(path)

    def get_file_array(self):
        """Return the array of data files"""
        return self.data_files

    def get_file_id_iterator(self):
        """Return an iterator over the int identifiers of all data files"""
        return range(0, len(self.data_files))


def dict_value(dictionary, key):
    """Return the value of dictionary for key or None if it doesn't exist"""
    if not dictionary:
        return None
    try:
        return dictionary[key]
    except KeyError:
        return None


def array_value(array, index):
    """Return the value of array at index or None if it doesn't exist"""
    try:
        return array[index]
    except (IndexError, TypeError):
        return None


def author_orcid(row):
    """Return the ISNI part of an ORCID URL or None if missing"""
    orcid = row.get("ORCID")
    if orcid:
        return orcid[17:]
    return None


def boolean_value(dictionary, key):
    """Return 0, 1, or None for the corresponding JSON value for key k
    of dict d"""
    if not dictionary:
        return None
    try:
        value = dictionary[key]
    except KeyError:
        return None
    if value:
        return 1
    return 0


def issn_value(dictionary, issn_type):
    """Return the ISSN of the specified type from a row that may contain
    an issn-type entry"""
    if not dictionary:
        return None
    try:
        # Array of entries like { "type": "electronic" , "value": "1756-2848" }
        type_values = dictionary["issn-type"]
    except KeyError:
        return None
    value = [tv["value"] for tv in type_values if tv["type"] == issn_type]
    return value[0] if value else None


def len_value(dictionary, key):
    """Return array length or None for the corresponding JSON value for key k
    of dict d"""
    if not dictionary:
        return None
    try:
        value = dictionary[key]
    except KeyError:
        return None
    return len(value)


def first_value(array):
    """Return the first element of array a or None if it doesn't exist"""
    return array_value(array, 0)


def tab_values(array):
    """Return the elements of array a separated by tab or None if it doesn't
    exist"""
    if not array:
        return None
    return "\t".join(array)


def lower_or_none(str):
    """Return the string in lowercase or None if None is passed"""
    return str.lower() if str else None


class Source:
    """Virtual table data source.  This gets registered with the apsw
    Connection through createmodule in order to instantiate the virtual
    tables."""

    def __init__(self, table_dict, data_directory):
        self.data_files = DataFiles(data_directory)
        self.table_dict = table_dict

    def Create(self, _db, _module_name, _db_name, table_name):
        """Create the specified virtual table"""
        return self.table_dict[table_name].creation_tuple(
            self.table_dict, self.data_files.get_file_array()
        )

    Connect = Create

    def get_file_id_iterator(self):
        """Return an iterator over the data files' identifiers"""
        return self.data_files.get_file_id_iterator()


class WorksCursor:
    """A cursor over the works data."""

    def __init__(self, table):
        self.table = table
        self.files_cursor = FilesCursor(table)
        # Initialized in Filter()
        self.eof = None
        self.item_index = None

    def Eof(self):
        """Return True when the end of the table's records has been reached."""
        return self.eof

    def Rowid(self):
        """Return a unique id of the row along all records"""
        # Allow for 16k items per file (currently 5k)
        return (self.files_cursor.Rowid() << 14) | (self.item_index)

    def current_row_value(self):
        """Return the current row. Not part of the apsw API."""
        return self.files_cursor.current_row_value()[self.item_index]

    def container_id(self):
        """Return the id of the container containing the data being fetched.
        Not part of the apsw API."""
        return self.files_cursor.Rowid()

    def Column(self, col):
        """Return the value of the column with ordinal col"""
        if col == -1:
            return self.Rowid()

        if col == CONTAINER_ID_COLUMN:
            return self.container_id()

        extract_function = self.table.get_value_extractor_by_ordinal(col)
        return extract_function(self.current_row_value())

    def Filter(self, index_number, index_name, constraint_args):
        """Always called first to initialize an iteration to the first row
        of the table according to the index"""
        self.files_cursor.Filter(index_number, index_name, constraint_args)
        self.eof = self.files_cursor.Eof()
        # print("FILTER", index_number, constraint_args)
        if index_number & ROWID_INDEX:
            # This has never happened, so this is untested
            self.item_index = constraint_args[1]
        else:
            self.item_index = 0

    def Next(self):
        """Advance to the next item."""
        self.item_index += 1
        if self.item_index >= len(self.files_cursor.items):
            self.item_index = 0
            self.files_cursor.Next()
            self.eof = self.files_cursor.eof

    def Close(self):
        """Cursor's destructor, used for cleanup"""
        self.files_cursor.Close()


class ElementsCursor:
    """An (abstract) cursor over a collection of data embedded within
    another cursor."""

    __metaclass__ = abc.ABCMeta

    def __init__(self, table, parent_cursor):
        self.table = table
        self.parent_cursor = parent_cursor
        self.elements = None
        self.eof = None
        # Initialized in Filter()
        self.element_index = None

    @abc.abstractmethod
    def element_name(self):
        """The work key from which to retrieve the elements. Not part of the
        apsw API."""
        return

    def Filter(self, *args):
        """Always called first to initialize an iteration to the first row
        of the table"""
        self.parent_cursor.Filter(*args)
        self.elements = None
        self.Next()

    def Eof(self):
        """Return True when the end of the table's records has been reached."""
        return self.eof

    @abc.abstractmethod
    def Rowid(self):
        """Return a unique id of the row along all records"""
        return

    def record_id(self):
        """Return the record's identifier. Not part of the apsw API."""
        return self.Rowid()

    def current_row_value(self):
        """Return the current row. Not part of the apsw API."""
        return self.elements[self.element_index]

    def Next(self):
        """Advance reading to the next available element."""
        while True:
            if self.parent_cursor.Eof():
                self.eof = True
                return
            if not self.elements:
                self.elements = self.parent_cursor.current_row_value().get(
                    self.element_name()
                )
                self.element_index = -1
            if not self.elements:
                self.parent_cursor.Next()
                self.elements = None
                continue
            if self.element_index + 1 < len(self.elements):
                self.element_index += 1
                self.eof = False
                return
            self.parent_cursor.Next()
            self.elements = None

    def container_id(self):
        """Return the id of the container containing the data being fetched.
        Not part of the apsw API."""
        return self.parent_cursor.container_id()

    def Column(self, col):
        """Return the value of the column with ordinal col"""
        if col == -1:
            return self.Rowid()

        if col == CONTAINER_ID_COLUMN:
            return self.container_id()

        extract_function = self.table.get_value_extractor_by_ordinal(col)
        return extract_function(self.current_row_value())

    def Close(self):
        """Cursor's destructor, used for cleanup"""
        self.parent_cursor.Close()
        self.elements = None


class AuthorsCursor(ElementsCursor):
    """A cursor over the items' authors data."""

    def element_name(self):
        """The work key from which to retrieve the elements. Not part of the
        apsw API."""
        return "author"

    def Rowid(self):
        """Return a unique id of the row along all records.
        This allows for 16k authors. There is a Physics paper with 5k
        authors."""
        return (self.parent_cursor.Rowid() << 14) | self.element_index

    def Column(self, col):
        """Return the value of the column with ordinal col"""
        if col == 0:  # id
            return self.record_id()

        if col == 2:  # work_doi
            return self.parent_cursor.current_row_value().get("DOI").lower()

        return super().Column(col)


class ReferencesCursor(ElementsCursor):
    """A cursor over the items' references data."""

    def element_name(self):
        """The work key from which to retrieve the elements. Not part of the
        apsw API."""
        return "reference"

    def Rowid(self):
        """Return a unique id of the row along all records.
        This allows for 1M references"""
        return (self.parent_cursor.Rowid() << 20) | self.element_index

    def Column(self, col):
        if col == 0:  # work_doi
            return self.parent_cursor.current_row_value().get("DOI").lower()
        return super().Column(col)


class UpdatesCursor(ElementsCursor):
    """A cursor over the items' updates data."""

    def element_name(self):
        """The work key from which to retrieve the elements. Not part of the
        apsw API."""
        return "update-to"

    def Rowid(self):
        """Return a unique id of the row along all records.
        This allows for 1M updates"""
        return (self.parent_cursor.Rowid() << 20) | self.element_index

    def Column(self, col):
        if col == 0:  # work_doi
            return self.parent_cursor.current_row_value().get("DOI").lower()
        return super().Column(col)


class SubjectsCursor(ElementsCursor):
    """A cursor over the work items' subject data."""

    def element_name(self):
        """The work key from which to retrieve the elements. Not part of the
        apsw API."""
        return "subject"

    def Rowid(self):
        """Return a unique id of the row along all records.
        This allows for 1M subjects"""
        return (self.parent_cursor.Rowid() << 20) | self.element_index

    def Column(self, col):
        """Return the value of the column with ordinal col"""
        if col == 0:  # work_doi
            return self.parent_cursor.current_row_value().get("DOI").lower()
        return super().Column(col)


class FundersCursor(ElementsCursor):
    """A cursor over the work items' funder data."""

    def element_name(self):
        """The work key from which to retrieve the elements. Not part of the
        apsw API."""
        return "funder"

    def Rowid(self):
        """Return a unique id of the row along all records
        This allows for 1k funders"""
        return (self.parent_cursor.Rowid() << 10) | self.element_index

    def Column(self, col):
        """Return the value of the column with ordinal col"""
        if col == 0:  # id
            return self.record_id()

        if col == 2:  # work_doi
            return self.parent_cursor.current_row_value().get("DOI").lower()

        return super().Column(col)


class AffiliationsCursor(ElementsCursor):
    """A cursor over the authors' affiliation data."""

    def element_name(self):
        """The work key from which to retrieve the elements. Not part of the
        apsw API."""
        return "affiliation"

    def Rowid(self):
        """Return a unique id of the row along all records
        This allows for 128 affiliations per author."""
        return (self.parent_cursor.Rowid() << 7) | self.element_index

    def Column(self, col):
        """Return the value of the column with ordinal col"""
        if col == 0:  # Author-id
            return self.parent_cursor.record_id()
        return super().Column(col)


class AwardsCursor(ElementsCursor):
    """A cursor over the authors' affiliation data."""

    def element_name(self):
        """The work key from which to retrieve the elements. Not part of the
        apsw API."""
        return "award"

    def Rowid(self):
        """Return a unique id of the row along all records
        This allows for 1k awards per funder."""
        return (self.parent_cursor.Rowid() << 10) | self.element_index

    def Column(self, col):
        """Return the value of the column with ordinal col"""
        if col == 0:  # Funder-id
            return self.parent_cursor.record_id()
        return super().Column(col)


# By convention column 0 is the unique or foreign key,
# and column 1 the data's container
tables = [
    TableMeta(
        "works",
        cursor_class=WorksCursor,
        columns=[
            ColumnMeta("DOI", lambda row: dict_value(row, "DOI").lower()),
            ColumnMeta("container_id"),
            ColumnMeta(
                "title", lambda row: tab_values(dict_value(row, "title"))
            ),
            ColumnMeta(
                "published_year",
                lambda row: array_value(
                    first_value(
                        dict_value(dict_value(row, "published"), "date-parts")
                    ),
                    0,
                ),
            ),
            ColumnMeta(
                "published_month",
                lambda row: array_value(
                    first_value(
                        dict_value(dict_value(row, "published"), "date-parts")
                    ),
                    1,
                ),
            ),
            ColumnMeta(
                "published_day",
                lambda row: array_value(
                    first_value(
                        dict_value(dict_value(row, "published"), "date-parts")
                    ),
                    2,
                ),
            ),
            ColumnMeta(
                "short_container_title",
                lambda row: tab_values(
                    dict_value(row, "short-container-title")
                ),
            ),
            ColumnMeta(
                "container_title",
                lambda row: tab_values(dict_value(row, "container-title")),
            ),
            ColumnMeta("publisher", lambda row: dict_value(row, "publisher")),
            ColumnMeta("abstract", lambda row: dict_value(row, "abstract")),
            ColumnMeta("type", lambda row: dict_value(row, "type")),
            ColumnMeta("subtype", lambda row: dict_value(row, "subtype")),
            ColumnMeta("page", lambda row: dict_value(row, "page")),
            ColumnMeta("volume", lambda row: dict_value(row, "volume")),
            ColumnMeta(
                "article_number", lambda row: dict_value(row, "article-number")
            ),
            ColumnMeta(
                "journal_issue",
                lambda row: dict_value(
                    dict_value(row, "journal-issue"), "issue"
                ),
            ),
            ColumnMeta("issn_print", lambda row: issn_value(row, "print")),
            ColumnMeta(
                "issn_electronic", lambda row: issn_value(row, "electronic")
            ),
            # Synthetic column, which can be used for population filtering
            ColumnMeta(
                "update_count", lambda row: len_value(row, "update-to")
            ),
        ],
    ),
    TableMeta(
        "work_authors",
        foreign_key="work_doi",
        parent_name="works",
        primary_key="doi",
        cursor_class=AuthorsCursor,
        columns=[
            ColumnMeta("id"),
            ColumnMeta("container_id"),
            ColumnMeta("work_doi"),
            ColumnMeta("orcid", author_orcid),
            ColumnMeta("suffix", lambda row: dict_value(row, "suffix")),
            ColumnMeta("given", lambda row: dict_value(row, "given")),
            ColumnMeta("family", lambda row: dict_value(row, "family")),
            ColumnMeta("name", lambda row: dict_value(row, "name")),
            ColumnMeta(
                "authenticated_orcid",
                lambda row: boolean_value(row, "authenticated-orcid"),
            ),
            ColumnMeta("prefix", lambda row: dict_value(row, "prefix")),
            ColumnMeta("sequence", lambda row: dict_value(row, "sequence")),
        ],
    ),
    TableMeta(
        "author_affiliations",
        foreign_key="author_id",
        parent_name="work_authors",
        primary_key="id",
        cursor_class=AffiliationsCursor,
        columns=[
            ColumnMeta("author_id"),
            ColumnMeta("container_id"),
            ColumnMeta("name", lambda row: dict_value(row, "name")),
        ],
    ),
    TableMeta(
        "work_references",
        foreign_key="work_doi",
        parent_name="works",
        primary_key="doi",
        cursor_class=ReferencesCursor,
        columns=[
            ColumnMeta("work_doi"),
            ColumnMeta("container_id"),
            ColumnMeta("issn", lambda row: dict_value(row, "issn")),
            ColumnMeta(
                "standards_body", lambda row: dict_value(row, "standards-body")
            ),
            ColumnMeta("issue", lambda row: dict_value(row, "issue")),
            ColumnMeta("key", lambda row: dict_value(row, "key")),
            ColumnMeta(
                "series_title", lambda row: dict_value(row, "series-title")
            ),
            ColumnMeta("isbn_type", lambda row: dict_value(row, "isbn-type")),
            ColumnMeta(
                "doi_asserted_by",
                lambda row: dict_value(row, "doi-asserted-by"),
            ),
            ColumnMeta(
                "first_page", lambda row: dict_value(row, "first-page")
            ),
            ColumnMeta("isbn", lambda row: dict_value(row, "isbn")),
            ColumnMeta(
                "doi", lambda row: lower_or_none(dict_value(row, "DOI"))
            ),
            ColumnMeta("component", lambda row: dict_value(row, "component")),
            ColumnMeta(
                "article_title", lambda row: dict_value(row, "article-title")
            ),
            ColumnMeta(
                "volume_title", lambda row: dict_value(row, "volume-title")
            ),
            ColumnMeta("volume", lambda row: dict_value(row, "volume")),
            ColumnMeta("author", lambda row: dict_value(row, "author")),
            ColumnMeta(
                "standard_designator",
                lambda row: dict_value(row, "standard-designator"),
            ),
            ColumnMeta("year", lambda row: dict_value(row, "year")),
            ColumnMeta(
                "unstructured", lambda row: dict_value(row, "unstructured")
            ),
            ColumnMeta("edition", lambda row: dict_value(row, "edition")),
            ColumnMeta(
                "journal_title", lambda row: dict_value(row, "journal-title")
            ),
            ColumnMeta("issn_type", lambda row: dict_value(row, "issn-type")),
        ],
    ),
    TableMeta(
        "work_updates",
        foreign_key="work_doi",
        parent_name="works",
        primary_key="doi",
        cursor_class=UpdatesCursor,
        columns=[
            ColumnMeta("work_doi"),
            ColumnMeta("container_id"),
            ColumnMeta("label", lambda row: dict_value(row, "label")),
            ColumnMeta(
                "doi", lambda row: lower_or_none(dict_value(row, "DOI"))
            ),
            ColumnMeta(
                "timestamp",
                lambda row: dict_value(
                    dict_value(row, "updated"), "timestamp"
                ),
            ),
        ],
    ),
    TableMeta(
        "work_subjects",
        foreign_key="work_doi",
        parent_name="works",
        primary_key="doi",
        cursor_class=SubjectsCursor,
        columns=[
            ColumnMeta("work_doi"),
            ColumnMeta("container_id"),
            ColumnMeta("name", lambda row: row),
        ],
    ),
    TableMeta(
        "work_funders",
        foreign_key="work_doi",
        parent_name="works",
        primary_key="doi",
        cursor_class=FundersCursor,
        columns=[
            ColumnMeta("id"),
            ColumnMeta("container_id"),
            ColumnMeta("work_doi"),
            ColumnMeta(
                "doi", lambda row: lower_or_none(dict_value(row, "DOI"))
            ),
            ColumnMeta("name", lambda row: dict_value(row, "name")),
        ],
    ),
    TableMeta(
        "funder_awards",
        foreign_key="funder_id",
        parent_name="work_funders",
        primary_key="id",
        cursor_class=AwardsCursor,
        columns=[
            ColumnMeta("funder_id"),
            ColumnMeta("container_id"),
            ColumnMeta("name", lambda row: row),
        ],
    ),
]

table_dict = {t.get_name(): t for t in tables}


def get_table_meta_by_name(name):
    """Return the metadata of the specified table"""
    return table_dict[name]
