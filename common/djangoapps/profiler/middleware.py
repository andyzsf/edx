"""
Hotshot/CProfile Profiler Middleware

- Original version taken from http://www.djangosnippets.org/snippets/186/
- Original author: udfalkso
- Modified by: Shwagroo Team, Gun.io, and many others
- This version implemented by the Solutions Team @ edX.org
- Various bits derived from Internet references, primarily:
   * https://code.djangoproject.com/wiki/ProfilingDjango
   * https://gun.io/blog/fast-as-fuck-django-part-1-using-a-profiler/
   * https://blog.safaribooksonline.com/2013/11/21/profiling-django-via-middleware/
   * http://www.jeffknupp.com/blog/2012/02/14/profiling-django-applications-a-journey-from-1300-to-2-queries/
- The profiler is enabled via feature flag in settings.py -- see devstack.py and test.py
- Once enabled, simply add "prof=1" to the query string to profile your view
- Include "&profiler_mode=help" for more information
- e.g. http://localhost:8000/about?prof=1&profiler_mode=help

"""
from abc import ABCMeta, abstractmethod, abstractproperty
import hotshot
import hotshot.stats
import logging
import pstats
import sys
import tempfile
from threading import local

from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed

from helpers import (
    generate_help, generate_console_response, generate_text_response,
    generate_html_response, generate_pdf_response, generate_svg_response,
    generate_raw_response, summary_for_files
)

try:
    import cProfile
    HAS_CPROFILE = True
except ImportError:
    HAS_CPROFILE = False

try:
    import cStringIO as StringIO
except ImportError:
    import StringIO

# log = logging.getLogger(__name__)
log = logging.getLogger('profiler.middleware')

TLS = local()

from pdb import set_trace

class Profile(object):
    """
    Profile config and metadata
    """
    def __init__(self, profiler_requested):
        self.requested = profiler_requested
        self.type = None
        self.data_file = None
        self.profiler = None


class BaseProfilerMiddleware(object):
    """
    Abstract base classs for profiler middleware.

    This class performs the actual work of profiling and generating the
    report output.
    The child classes address implementation-specific idiosyncrasies for each profiler.
    """
    __metaclass__ = ABCMeta

    @abstractproperty
    def profiler_type(self):
        """
        Which profiler this is
        """
        raise NotImplementedError('Subclasses must implement profiler_type')

    @abstractproperty
    def is_profiler_installed(self):
        """
        Is this profiler installed?
        """
        return False

    @abstractmethod
    def profiler_start(self):
        """
        Method for starting
        """
        raise NotImplementedError('Subclasses must implement profiler_start')

    @abstractmethod
    def profiler_stop(self, _stream):
        """
        Parent method
        """
        raise NotImplementedError('Subclasses must implement profiler_stop')

    @staticmethod
    def _do_cleanup():
        """
        Clean up the stuff we stored in the thread local storage
        """
        log.info('Doing the cleanups')
        TLS.prof.type = None
        TLS.prof.requested = None

        if TLS.prof.data_file:
            log.info('Closing the temp file')
            TLS.prof.data_file.close()  # Closing the temp file will automatically delete it.
        TLS.prof.data_file = None


    def process_request(self, request):
        """
        Set up the profiler for use
        """
        log.info('Entering process_request')

        # Capture some values/references to use across the operations into thread local storage
        profiler_requested = request.GET.get('prof', False)
        TLS.prof = Profile(profiler_requested)

        # Check if the request asked to be profiled
        if not TLS.prof.requested:
            log.info('Profiling was not requested.')
            return

        # Ensure we're allowed to use the profiler
        if not settings.DEBUG and not request.user.is_superuser:
            msg = '{}{}'.format(
                'The DEBUG environment parameter must be set to True, or the ',
                'authenticated user must be configured as a superuser to use the profiler middleware.'
            )
            log.info(msg)
            raise MiddlewareNotUsed(msg)

        # Set the profiler type to the requested (or default) one
        if TLS.prof.type is None:
            TLS.prof.type = request.GET.get('profiler_type', 'hotshot')

        # Check if this is the profiler that was requested
        if self.profiler_type is not TLS.prof.type:
            msg = 'Requesting {} profiler, this is {} profiler'.format(
                TLS.prof.type, self.profiler_type
            )
            log.info(msg)
            raise MiddlewareNotUsed(msg)

        log.info('Processing with requested profiler: {}'.format(self.profiler_type))

        # Ensure the profiler being requested is actually installed/available
        if not self.is_profiler_installed:
            msg = '{} profiler is not installed'.format(self.profiler_type)
            log.info(msg)
            raise MiddlewareNotUsed(msg)

        # Create the container we'll be using to store the raw profiler data
        if TLS.prof.data_file is None:
            log.info('Creating temporary data file')
            TLS.prof.data_file = tempfile.NamedTemporaryFile(delete=True)
            log.info('{} file created'.format(TLS.prof.data_file.name))

    def process_view(self, request, callback, callback_args, callback_kwargs):
        """
        Enable the profiler and begin collecting data about the view
        Note that this misses the rest of Django's request processing (other middleware, etc.)
        """
        log.info('Entering process_view')

        # Check if the request asked to be profiled
        if not TLS.prof.requested:
            log.info('Profiling was not requested.')
            return

        log.info('Starting profiler {}'.format(self.profiler_type))
        TLS.prof.profiler = self.profiler_start()
        return TLS.prof.profiler.runcall(callback, request, *callback_args, **callback_kwargs)


    def process_response(self, request, response):
        """
        Most of the heavy lifting takes place in this base process_response operation
        It seems process_response can be invoked without a prior invocation
        of process request and/or process view, so we need to put in a guard
        """
        log.info('Entering process_response')

        # Check if the request asked to be profiled
        if not TLS.prof.requested:
            log.info('Profiling was not requested.')
            return response

        # Set the profiler type to the requested (or default) one
        if TLS.prof.type is None:
            TLS.prof.type = request.GET.get('profiler_type', 'hotshot')

        log.info('{} profiler was requested'.format(TLS.prof.type))
        log.info('This is the {} profiler'.format(self.profiler_type))
        log.info('Profiler exists: {}'.format(TLS.prof.profiler is not None))

        # Only process if this is the profiler that you wanted to use
        if self.profiler_type == TLS.prof.type and TLS.prof.profiler is not None:

            # Ensure the profiler being requested is actually installed/available
            if not self.is_profiler_installed:
                msg = '{} profiler is not installed'.format(self.profiler_type)
                log.info(msg)
                raise MiddlewareNotUsed(msg)

            # The caller may want to view the runtime help documentation
            profiler_mode = request.GET.get('profiler_mode', 'normal')
            if profiler_mode == 'help':
                response['Content-Type'] = 'text/plain'
                response.content = generate_help()
                self._do_cleanup()
                return response

            # Set up a redirected stdout location (hides output from console)
            old_stdout = sys.stdout
            temp_stdout = StringIO.StringIO()
            sys.stdout = temp_stdout

            # Load the statistics collected by the profiler
            stats = self.profiler_stop(temp_stdout)

            # Sort the statistics according to the caller's wishes
            # See http://docs.python.org/2/library/profile.html#pstats.Stats.sort_stats
            # for the all of the fields you can sort on
            profiler_sort = request.GET.get('profiler_sort', 'time')
            if profiler_sort == 'time':
                profiler_sort = 'time,calls'
            stats.sort_stats(*profiler_sort.split(','))

            # Strip out the directories from the report, if so desired
            strip_dirs = request.GET.get('profiler_strip', False)
            if strip_dirs:
                stats.strip_dirs()

            # Pare down the statistics data further, if specified
            restrictions = []
            # Regex filter
            if request.GET.get('profile_filter'):
                restrictions.append(request.GET['profile_filter'])
            # Cut the list down to either a fraction of the set or a specific line count
            if request.GET.get('profile_fraction'):
                restrictions.append(float(request.GET['profile_fraction']))
            elif request.GET.get('profile_lines'):
                restrictions.append(int(request.GET['profile_lines']))
            # If no size restriction and we're not filtering, trim stats to a reasonable amount
            elif not request.GET.get('filter'):
                restrictions.append(.1)

            # Send the statistics data to the redirected stdout location,
            # then put stdout back to its original configuration
            stats.print_stats(*restrictions)
            stats_str = temp_stdout.getvalue()
            sys.stdout.close()
            sys.stdout = old_stdout

            # Format the response
            if response and response.content and stats_str:
                stats_summary = summary_for_files(stats_str)
                response_format = request.GET.get('profiler_format', 'console')
                # Console format sends the profiler result to stdout, preserving current response content
                # All other formats replace response content with the profiler result
                if response_format == 'console':
                    generate_console_response(stats_str, stats_summary)
                elif response_format == 'text':
                    generate_text_response(stats_str, stats_summary, response)
                elif response_format == 'html':
                    generate_html_response(stats_str, stats_summary, response)
                elif response_format == 'pdf':
                    generate_pdf_response(TLS.data_file.name, response)
                elif response_format == 'svg':
                    generate_svg_response(self.profiler_type, TLS.data_file.name, response)
                elif response_format == 'raw':
                    generate_raw_response(self.profiler_type, TLS.data_file.name, response)

        self._do_cleanup()

        return response


class HotshotProfilerMiddleware(BaseProfilerMiddleware):
    """
    Hotshot is a replacement for the existing profile module.
    See https://docs.python.org/2/library/hotshot.html for more info
    WARNING: The Hotshot profiler is not thread safe.
    """
    @property
    def profiler_type(self):
        """
        Use this value to select the profiler via query string
        """
        return 'hotshot'

    @property
    def is_profiler_installed(self):
        """
        Hotshot is native and available for use
        """
        return True

    def profiler_start(self):
        """
        Turn on the profiler and begin collecting data
        """
        log.info('Starting with file: {}'.format(TLS.prof.data_file.name))
        return hotshot.Profile(TLS.prof.data_file.name)

    def profiler_stop(self, _stream):
        """
        Store profiler data in file and return statistics to caller
        """
        TLS.prof.profiler.close()
        return hotshot.stats.load(TLS.prof.data_file.name)


class CProfileProfilerMiddleware(BaseProfilerMiddleware):
    """
    CProfile is a runtime profiler available natively in Python
    See https://docs.python.org/2/library/profile.html#module-cProfile for more info
    """
    @property
    def profiler_type(self):
        """
        Use this value to select the profiler via query string
        """
        return 'cprofile'

    @property
    def is_profiler_installed(self):
        """
        Apparently CProfile is not native, and many examples simply
        failover to the regular 'profile' module.  Maybe we should, too.
        """
        return HAS_CPROFILE

    def profiler_start(self):
        """
        Turn on the profiler and begin collecting data
        """
        return cProfile.Profile()

    def profiler_stop(self, _stream):
        """
        Store profiler data in file and return statistics to caller
        """
        TLS.prof.profiler.create_stats()
        TLS.prof.profiler.dump_stats(TLS.prof.data_file.name)
        return pstats.Stats(TLS.prof.profiler, stream=_stream)
