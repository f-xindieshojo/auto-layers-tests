"""Minimal TAP runner for unittest suites."""
import sys
import unittest


class TAPResult(unittest.TestResult):
    def __init__(self, stream):
        super().__init__()
        self.stream = stream
        self._n = 0

    def _line(self, ok, test, err=None):
        self._n += 1
        if ok:
            self.stream.write(f"ok {self._n} - {test.id()}\n")
        else:
            self.stream.write(f"not ok {self._n} - {test.id()}\n")
            for line in self._exc_info_to_string(err, test).splitlines():
                self.stream.write(f"# {line}\n")
        self.stream.flush()

    def addSuccess(self, test):
        super().addSuccess(test)
        self._line(True, test)

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._line(False, test, err)

    def addError(self, test, err):
        super().addError(test, err)
        self._line(False, test, err)

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._n += 1
        self.stream.write(f"ok {self._n} - {test.id()} # SKIP {reason}\n")
        self.stream.flush()

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._n += 1
        self.stream.write(f"ok {self._n} - {test.id()} # TODO expected failure\n")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._n += 1
        self.stream.write(f"not ok {self._n} - {test.id()}\n")
        self.stream.flush()


def run(suite, stream=sys.stdout):
    result = TAPResult(stream)
    suite.run(result)
    passed = result.testsRun - len(result.failures) - len(result.errors) - len(result.unexpectedSuccesses) - len(result.skipped)
    failed = len(result.failures) + len(result.errors) + len(result.unexpectedSuccesses)
    stream.write(f"# pass {passed}\n")
    stream.write(f"# fail {failed}\n")
    stream.flush()
    return 0 if result.wasSuccessful() else 1
