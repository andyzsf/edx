"""
Utility methods related to file handling.
"""

from datetime import datetime
import os
from pytz import UTC
import urllib

from django.core import exceptions
from django.core.files.storage import get_storage_class
from django.utils.translation import ugettext as _
from django.utils.translation import ungettext


def store_uploaded_file(
        request, file_key, allowed_file_types, base_storage_filename, max_file_size=10240000, validator=None,
):
    """
    Stores an uploaded file to django file storage.

    Args:
        request (HttpRequest): A request object from which a file will be retrieved.
        file_key (str): The key for retrieving the file from `request.FILES`. If no entry exists with this
            key, a `ValueError` will be thrown.
        allowed_file_types (list): a list of allowable file type extensions. These should start with a period
            and be specified in lower-case. For example, ['.txt', '.csv']. If the uploaded file does not end
            with one of these extensions, a `PermissionDenied` exception will be thrown. Note that the uploaded file
            extension does not need to be lower-case.
        base_storage_filename (str): the filename to be used for the stored file, not including the extension.
            The same extension as the uploaded file will be appended to this value.
        max_file_size (int): the maximum file size in bytes that the uploaded file can be. If the uploaded file
            is larger than this size, a `PermissionDenied` exception will be thrown. Default value is 10240000 bytes
            (10,000 KB).
        validator (function): an optional validation method that, if defined, will be passed the stored file (which
            is copied from the uploaded file). This method can do validation on the contents of the file and throw
            an exception if the file is not properly formatted. If an exception is thrown, the stored file will
            be deleted before the exception is re-raised. Note that the implementor of the validator function should
            take care to close the stored file if they open it for reading.

    Returns:
        Storage: the file storage object where the file can be retrieved from
        str: stored_file_name: the name of the stored file (including extension)

    """

    if file_key not in request.FILES:
        raise ValueError(_("No file uploaded with key '" + file_key + "'."))

    uploaded_file = request.FILES[file_key]
    try:
        file_extension = os.path.splitext(uploaded_file.name)[1].lower()
        if not file_extension in allowed_file_types:
            file_types = "', '".join(allowed_file_types)
            msg = ungettext(
                "The file must end with the extension '{file_types}'.",
                "The file must end with one of the following extensions: '{file_types}'.",
                len(allowed_file_types)).format(file_types=file_types)
            raise exceptions.PermissionDenied(msg)

        stored_file_name = base_storage_filename + file_extension

        file_storage = get_storage_class()()
        # use default storage to store file
        file_storage.save(stored_file_name, uploaded_file)

        if validator:
            try:
                validator(file_storage, stored_file_name)
            except Exception as exception:
                file_storage.delete(stored_file_name)
                raise exception

    finally:
        uploaded_file.close()

    # check file size
    size = file_storage.size(stored_file_name)
    if size > max_file_size:
        file_storage.delete(stored_file_name)
        msg = _("Maximum upload file size is {file_size} bytes.").format(file_size=max_file_size)
        raise exceptions.PermissionDenied(msg)

    return file_storage, stored_file_name


# pylint: disable=invalid-name
def course_and_time_based_filename_generator(course_id, base_name):
    """
    Generates a filename (without extension) based on the current time and the supplied filename.

    Args:
        course_id (object): A course identification object that must support conversion to unicode.
        base_name (str): A name describing what type of file this is. Should not include any characters
            that are not safe for filenames.

    Returns:
        str: a concatenation of the course_id (with backslashes replace by underscores), the base_name,
            and the current time. Note that there will be no extension.

    """
    return u"{course_prefix}_{base_name}_{timestamp_str}".format(
        course_prefix=urllib.quote(unicode(course_id).replace("/", "_")),
        base_name=base_name,
        timestamp_str=datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S")  # pylint: disable=maybe-no-member
    )


# TODO: refactor... from http://d4nt.com/parsing-large-csv-blobs-on-google-app-engine/
class BlobIterator(object):
    """Because the python csv module doesn't like strange newline chars and
    the google blob reader cannot be told to open in universal mode, then
    we need to read blocks of the blob and 'fix' the newlines as we go"""

    def __init__(self, blob_reader):
        self.blob_reader = blob_reader
        self.last_line = ""
        self.line_num = 0
        self.lines = []
        self.buffer = None

    def __iter__(self):
        return self

    def next(self):
        if not self.buffer or len(self.lines) == self.line_num + 1:
            self.buffer = self.blob_reader.read(1048576 / 4)  # 1/4 MB buffer
            self.lines = self.buffer.splitlines()
            self.line_num = 0

            # Handle special case where our block just happens to end on a new line
            if self.buffer[-1:] == "\n" or self.buffer[-1:] == "\r":
                self.lines.append("")

        if not self.buffer:
            # When we've exhausted the source file, return the last line.
            if self.last_line:
                result = self.last_line
                self.last_line = ''
                return result
            else:
                raise StopIteration

        if self.line_num == 0 and len(self.last_line) > 0:
            result = self.last_line + self.lines[self.line_num] + "\n"
        else:
            result = self.lines[self.line_num] + "\n"

        self.last_line = self.lines[self.line_num + 1]
        self.line_num += 1

        return result
