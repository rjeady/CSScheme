import locale
import re
import os
import subprocess

import sublime
import sublime_plugin

from sublime_lib.view import OutputPanel

try:
    from .tinycsscheme.parser import CSSchemeParser, ParseError
    from .tinycsscheme.dumper import CSSchemeDumper, DumpError
except:
    from tinycsscheme.parser import CSSchemeParser, ParseError
    from tinycsscheme.dumper import CSSchemeDumper, DumpError


def normalize_stdout(source):
    # or 'utf-8'?
    return source.decode(locale.getpreferredencoding()).replace('\r\n', '\n')


# Returns a function for use with `re.sub`, requires matches in groups 1 and 2
def swap_path_line(pattern, rel_dir):
    def repl(m):
        # Make path relative because we don't need long paths if in same dir
        path = os.path.relpath(m.group(2), rel_dir)
        return pattern % (path, m.group(1))
    return repl


class convert_csscheme(sublime_plugin.TextCommand):
# class ConvertCSScheme(sublime_plugin.TextCommand):
    """docs
    """
    def is_enabled(self):
        print('enabled', self.get_in_ext() is not None)
        return self.get_in_ext() is not None

    def get_in_ext(self):
        if not self.view.file_name():
            return None
        m = re.search(r'\.((?:le|sc|sa|c)ss)cheme$', self.view.file_name())
        return m.group(1) if m else None

    def run(self, edit):
        if self.view.is_dirty():
            return sublime.status_message("Save file first")

        in_file = self.view.file_name()
        in_ext = self.get_in_ext()
        in_dir, in_base = os.path.split(in_file)
        out_file = os.path.splitext(in_file)[0] + '.tmTheme'

        # Get text data

        commands = dict(
            sass=['sass', '-l'],
            scss=['sass', '-l',  '--scss'],
            # less='less',  # TODO
            # stylus= ...
        )
        # import spdb ; spdb.start()

        # Open up output panel and auto-finalize it when we are done
        with OutputPanel(self.view.window(), "csscheme") as out:
            out.set_path(in_dir)
            text = ""
            if in_ext in commands:
                try:
                    process = subprocess.Popen(commands[in_ext] + [in_file],
                                               stdout=subprocess.PIPE,
                                               stderr=subprocess.PIPE,
                                               shell=True)
                    results = process.communicate()
                except Exception as e:
                    out.write_line("Error converting from %s to CSS:\n"
                                   "%s: %s" % (in_ext, e.__class__.__name__, e))
                    return
                # print(results)

                if results[1]:
                    out.set_regex(r"^\s+in (.*?) on line (\d+)$")

                    stderr = normalize_stdout(results[1])
                    out.write_line("Errors converting from %s to CSS:" % in_ext)
                    # Swap line and path because sublime can't parse them otherwise
                    out.write_line(re.sub(r"on line (\d+) of (.*?)$",
                                          swap_path_line(r"in %s on line %s", in_dir),
                                          stderr,
                                          flags=re.M))
                    return

                if results[0] is None:
                    out.write_line("Unexpected error converting from %s to CSS:\nNo output"
                                   % in_ext)
                    return
                text = normalize_stdout(results[0])
            else:
                assert in_ext == 'css'
                text = self.view.substr(sublime.Region(0, self.view.size()))

            # DEBUG
            if in_ext != 'css':
                v = self.view.window().new_file()
                v.set_scratch(True)
                v.set_syntax_file("Packages/CSS/CSS.tmLanguage")
                from sublime_lib.edit import Edit
                with Edit(v) as edit:
                    edit.append(text)

            # Parse the CSS
            stylesheet = CSSchemeParser().parse_stylesheet(text)

            # Do some awesome error printing action
            if stylesheet.errors:
                if in_ext in ('sass', 'scss'):
                    err_reg = re.compile(r"/\* line (\d+), (.*?) \*/", re.M)

                    # Match our modified output
                    out.set_regex(r"^\s*/\* (.*?), line (\d+) \*/")

                    lines = text.split('\n')
                    # I could wrap this in an Edit(out.view) call because I modify it so often
                    for e in stylesheet.errors:
                        assert isinstance(e, ParseError)
                        out.write_line("ParseError from CSS on line %d:" % e.line)

                        # Search for last known line number (max 10)
                        start_dump = 0
                        for i in range(e.line, e.line - 10, -1):
                            if i < 0:
                                break
                            m = re.match(r"\s*/\* line (\d+),", lines[i])
                            if not m:
                                continue
                            start_dump = i
                            # Swap line and path because sublime can't parse them otherwise
                            out.write_line(
                                "  " + err_reg.sub(swap_path_line("/* %s, line %s */", in_dir),
                                                   lines[i])
                            )
                            break

                        # Nothing found in the past 10 lines
                        if not start_dump:
                            out.write_line()
                            continue

                        for i in range(start_dump + 1, e.line):
                            out.write_line("  " + lines[i])
                        # Mark the column where the error happened (since we don't have source code)
                        out.write_line("  %s^" % ('-' * (e.column - 1)))
                        out.write_line("%s\n" % (e.reason))

                elif in_ext == 'css':
                    out.set_regex(r"^(.*):(\d+):(\d+):$")
                    for e in stylesheet.errors:
                        out.write_line("%s:%s:%s:\n  %s\n"
                                       % (in_base, e.line, e.column, e.reason))
                return

            print("dumping")
            # Dump CSS data as plist into out_file
            try:
                CSSchemeDumper().dump_stylesheet_file(out_file, stylesheet)
            except DumpError as e:
                if in_ext == 'css':
                    out.set_regex(r"^(.*):(\d+):(\d+):$")
                    out.write_line("%s:%s:%s:\n  %s%s\n"
                                   % (in_base, e.line, e.column, e.reason, e.location))
                else:
                    # We can't accurately determine where the error occured (besides searching for
                    # the last referenced number like above, and that kinda sucks), so just use text
                    out.write_line("Error in data:\n  %s%s\n" % (e.reason, e.location))
                return

            # Open out_file
            self.view.window().open_file(out_file)