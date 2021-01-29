import random
import string
import logging
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

import discord_logs_uploader

# stop logger printing to terminal and enable debug level
discord_logs_uploader.logger.setLevel(logging.DEBUG)
discord_logs_uploader.logger.removeHandler(discord_logs_uploader.handler)


def random_string(length: int) -> str:
    letters = string.ascii_letters + string.digits
    return ''.join(random.choice(letters) for i in range(length))


class TestZipFileTesting(unittest.TestCase):
    def test_not_zip_files(self) -> None:
        archive_name = random_string(10)
        zip_name = f"{random_string(10)}.zip"
        with self.assertLogs() as logs:
            result = discord_logs_uploader.pre_test_zipfile(
                archive_name=archive_name,
                zip_name=zip_name,
            )

        self.assertFalse(result, "Arbitrary files are being detected as zip files")
        self.assertIn(
            'not a ZIP',
            logs.output[0],
            "Incorrect failure occurred, 'not a ZIP' log expected",
        )
        self.assertEqual(len(logs.output), 1, "Additional logging is occuring")

    def test_no_prefix(self) -> None:
        archive_name = f"{random_string(10)}.zip"
        zip_name = f"{random_string(10)}.zip"
        with self.assertLogs() as logs:
            result = discord_logs_uploader.pre_test_zipfile(
                archive_name=archive_name,
                zip_name=zip_name,
            )

        self.assertFalse(result, "Zip files without the team prefix are being processed")
        self.assertIn(
            "doesn't start with",
            logs.output[0],
            "Incorrect failure occurred, \"doesn't start with prefix\" log expected",
        )
        self.assertEqual(len(logs.output), 1, "Additional logging is occuring")

    def test_prefix(self) -> None:
        archive_name = f"{discord_logs_uploader.TEAM_PREFIX}{random_string(10)}.zip"
        zip_name = f"{random_string(10)}.zip"
        test_string = f"Testing zip file pre-testing ({random_string(10)})"
        with self.assertLogs() as logs:
            discord_logs_uploader.logger.info(test_string)
            result = discord_logs_uploader.pre_test_zipfile(
                archive_name=archive_name,
                zip_name=zip_name,
            )

        self.assertTrue(result, "Correctly named team zip files are being rejected")
        self.assertEqual(len(logs.output), 1, "Additional logging is occuring")
        self.assertIn(test_string, logs.output[0], "Logger is not printing correctly")


class TestMatchAnimationFiles(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        ctx = tempfile.TemporaryDirectory()
        self.tmpdir_name = ctx.__enter__()
        self.addCleanup(lambda: ctx.__exit__(None, None, None))
        self.tempdir = Path(self.tmpdir_name)

        valid_match_num = f"{random.randrange(999)}"
        invalid_match_num = random.choice(string.ascii_letters)

        self.valid_log = f"log-zone-{random.randrange(9)}-match-{valid_match_num}.txt"
        self.missing_log = f"log-zone-{random.randrange(9)}-match-{random.randrange(999)}.txt"
        self.invalid_log = f"log-zone-{random.randrange(9)}-match-{invalid_match_num}.txt"

        self.valid_log_files = []
        self.invalid_log_files = []

        # populate 'animations'
        animations = self.tempdir / 'animations'
        animations.mkdir()

        # populate valid match files
        for index in range(5):
            animation_file = f"match-{valid_match_num}.{random_string(3)}"
            (animations / animation_file).open('w').close()  # generate animation file
            if not animation_file.endswith('mp4'):
                self.valid_log_files.append(animations / animation_file)

        (animations / f"match-{valid_match_num}.mp4").open('w').close()  # generate video file

        # populate invalid match files
        for index in range(5):
            animation_file = f"match-{invalid_match_num}.{random_string(3)}"
            (animations / animation_file).open('w').close()  # generate animation file
            if not animation_file.endswith('mp4'):
                self.invalid_log_files.append(animations / animation_file)

        # generate video file
        (animations / f"match-{invalid_match_num}.mp4").open('w').close()

    def test_valid_log_name(self) -> None:
        with self.assertLogs() as logs:
            results = discord_logs_uploader.match_animation_files(
                self.valid_log,
                self.tempdir / 'animations',
            )

        # test logs, results
        self.assertEqual(len(logs.output), 1, "Additional logging is occuring")
        self.assertIn('Fetching animation files', logs.output[0], "Incorrect log message")
        self.assertListEqual(  # results contains all valid_log_files
            sorted(results),
            sorted(self.valid_log_files),
            "Some valid animation files were not returned",
        )
        for animation_file in results:  # no mp4 in results
            self.assertNotEqual(
                animation_file.suffix,
                'mp4',
                "Movie files should not be included in animation files",
            )

    def test_invalid_log_name(self) -> None:
        with self.assertLogs() as logs:
            results = discord_logs_uploader.match_animation_files(
                self.invalid_log,
                self.tempdir / 'animations',
            )

        # test logs, results
        self.assertEqual(len(logs.output), 1, "Additional logging is occuring")
        self.assertIn('Invalid match name', logs.output[0], "Incorrect log message")
        self.assertListEqual(  # results contains no files
            results,
            [],
            "No files should have been returned",
        )

    def test_match_found(self) -> None:
        with self.assertLogs() as logs:
            results = discord_logs_uploader.match_animation_files(
                self.missing_log,
                self.tempdir / 'animations',
            )

        # test logs, results
        self.assertEqual(len(logs.output), 1, "Additional logging is occuring")
        self.assertIn('Fetching animation files', logs.output[0], "Incorrect log message")
        self.assertListEqual(  # results contains no files
            results,
            [],
            "No files should have been returned",
        )


class TestExtractAnimations(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        ctx = tempfile.TemporaryDirectory()
        self.tmpdir_name = ctx.__enter__()
        self.addCleanup(lambda: ctx.__exit__(None, None, None))
        self.tempdir = Path(self.tmpdir_name)
        self.animation_data = random_string(100)

        # make animations.zip
        animations_name = self.tempdir / f'animations-{random_string(10)}.zip'
        with ZipFile(animations_name, 'w') as animations_zip:
            animations_zip.writestr('data.txt', self.animation_data)

        # make a logs zip
        logs_name = self.tempdir / f'team-SRZ-{random_string(10)}.zip'
        with ZipFile(logs_name, 'w') as logs_zip:
            logs_zip.writestr('data2.txt', random_string(100))

        # make combined.zip w/o animations.zip
        with ZipFile(self.tempdir / 'combined-logs.zip', 'w') as combined_zip:
            combined_zip.write(logs_name, logs_name.name)

        # make combined.zip w/ animations.zip
        with ZipFile(self.tempdir / 'combined-ani.zip', 'w') as combined_zip:
            combined_zip.write(logs_name, logs_name.name)
            combined_zip.write(animations_name, animations_name.name)

    def test_missing_animations(self) -> None:
        # w/o animations.zip
        with tempfile.TemporaryDirectory() as tmp_extract_name:
            tmp_extract = Path(tmp_extract_name)
            with ZipFile(self.tempdir / 'combined-logs.zip') as combined_zip:
                result = discord_logs_uploader.extract_animations(
                    combined_zip,
                    tmp_extract,
                    False,
                )

            # test return value, tmpdir contents
            self.assertFalse(result)
            self.assertListEqual(  # empty tmpdir
                sorted(tmp_extract.iterdir()),
                [],
                "No files should have been extracted",
            )

    def test_partial_extract(self) -> None:
        # fully_extract == false
        with tempfile.TemporaryDirectory() as tmp_extract_name:
            tmp_extract = Path(tmp_extract_name)
            test_string = f"Testing extracting animation zip ({random_string(10)})"
            with self.assertLogs() as logs:
                discord_logs_uploader.logger.info(test_string)
                with ZipFile(self.tempdir / 'combined-ani.zip') as combined_zip:
                    result = discord_logs_uploader.extract_animations(
                        combined_zip,
                        tmp_extract,
                        False,
                    )

            # test return value, log output, tmpdir contents
            self.assertTrue(result)
            self.assertEqual(len(logs.output), 1, "Additional logging is occuring")
            self.assertIn(test_string, logs.output[0], "Logger is not printing correctly")
            self.assertListEqual(  # animations.zip in tmpdir, nothing else
                sorted(tmp_extract.iterdir()),
                [tmp_extract / 'animations.zip'],
                "The animations zip should have been extracted an renamed to 'animations.zip'",
            )

    def test_full_extract(self) -> None:
        # fully_extract == true
        with tempfile.TemporaryDirectory() as tmp_extract_name:
            tmp_extract = Path(tmp_extract_name)
            with self.assertLogs() as logs:
                with ZipFile(self.tempdir / 'combined-ani.zip') as combined_zip:
                    result = discord_logs_uploader.extract_animations(
                        combined_zip,
                        tmp_extract,
                        True,
                    )

            # test return value, log output, tmpdir contents
            self.assertTrue(result)
            self.assertEqual(len(logs.output), 1, "Additional logging is occuring")
            self.assertIn(
                "Extracting animations.zip",
                logs.output[0],
                "Logger is not printing correctly",
            )
            # animations.zip & animations/ in tmpdir, nothing else
            self.assertListEqual(
                sorted(tmp_extract.iterdir()),
                sorted([tmp_extract / 'animations.zip', tmp_extract / 'animations/']),
                "Only 'animations.zip' and 'animations/' should be produced",
            )
            self.assertListEqual(  # only data.txt in animations/
                sorted((tmp_extract / 'animations').iterdir()),
                [tmp_extract / 'animations/data.txt'],
                "'animations.zip' was not correctly extracted",
            )
            # test contents of data.txt in animations/
            with (tmp_extract / 'animations/data.txt').open() as data:
                extracted_data = data.read()
            self.assertEqual(
                extracted_data,
                self.animation_data,
                "'data.tx' was corrupted",
            )
