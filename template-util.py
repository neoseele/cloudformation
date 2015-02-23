#!/usr/bin/python2
# -*- coding: utf-8 -*-
"""
Simple CLI tool to automate splitting and un-splitting Cloudformation
templates and related functions

@author: Jeff Hubbard <jhubbard@redhat.com>
"""

import os
import difflib
import json
import collections
import datetime
import re
import base64


DIR_NAME = os.path.dirname(os.path.abspath(__file__))
USER_DATA_DIR = "{0}/user-data-scripts".format(DIR_NAME)
USER_DATA_MAP_FILE = "{0}/TAG-MAP.json".format(USER_DATA_DIR)
FILES_DIR = "{0}/files".format(DIR_NAME)
USER_DATA_MAP = {}

def _get_user_data_map():
    global USER_DATA_MAP
    if os.path.isfile(USER_DATA_MAP_FILE):
        with open(USER_DATA_MAP_FILE) as file_handle:
            USER_DATA_MAP = json.load(file_handle)
    else:
        print("Error:  {0} does not exist, will not be able to map instance "
            "tags to UserData, your templates likely will not "
            "work.".format(USER_DATA_MAP_FILE))

_get_user_data_map()

def _purify_json(json_text, a_preserve_order=False):
    """ Ensure that 2 JSON objects are indented and formatted
    in exactly the same way for unified diff-ing.
    `json_text` - A string containing JSON-formatted data
    """
    json_data = json.loads(
        json_text, object_pairs_hook=collections.OrderedDict)
    return json.dumps(
        json_data, sort_keys=not a_preserve_order,
        separators=(",", ":"), indent=4)


def json_diff(a_file1, a_file2, a_assert=False):
    """ Diff 2 JSON files, ignoring whitespace, indentation, etc... """
    for file_name in (a_file1, a_file2):
        if not os.path.isfile(file_name):
            print("Error, {0} does not exist".format(file_name))
            exit(1)
    with open(a_file1) as file_handle:
        try:
            text1 = _purify_json(file_handle.read())
        except Exception as ex:
            print(ex)
            print("Error loading {0}, the file may not be valid "
                "JSON".format(a_file1))
            exit(1)
    with open(a_file2) as file_handle:
        try:
            text2 = _purify_json(file_handle.read())
        except Exception as ex:
            print(ex)
            print("Error loading {0}, the file may not be valid "
                "JSON".format(a_file2))
            exit(1)
    u_diff = list(difflib.unified_diff(
        text1.split("\n"), text2.split("\n"), a_file1, a_file2))
    if a_assert:
        assert(not u_diff)
    else:
        if u_diff:
            print("\n".join(u_diff))
        else:
            print("No difference.")


def _embed_files(a_struct):
    """ Embed files directly in UserData by base64 encoding them into
        a command to base64 decode into a file
    """
    for resource_name, resource in a_struct["Resources"].items():
        if resource["Type"] == "AWS::EC2::Instance" and \
        "UserData" in resource["Properties"]:
            user_data_list = resource["Properties"]["UserData"][
                "Fn::Base64"]["Fn::Join"][1]
            if isinstance(user_data_list[0], collections.OrderedDict):
                continue
            for line in user_data_list[:]:
                if isinstance(line, collections.OrderedDict):
                    continue
                regex_match = re.match(
                    r'\{\{EmbedFile=(.*)\|Name=(.*)\}\}', line.strip())
                if not regex_match:
                    continue
                file_name, output_file = regex_match.groups()
                file_path = "{0}/{1}".format(FILES_DIR, file_name)
                if not os.path.isfile(file_path):
                    print("Error:  Embedded file does not exist: "
                        "{0} , {1}".format(line, file_path))
                    exit(1)
                with open(file_path) as file_handle:
                    file_text = file_handle.read()
                    b64_str = base64.encodestring(file_text)
                    b64_str = json.dumps(b64_str)
                    decode_cmd = (
                        "python -c 'import base64 ; "
                        "file_handle = open(\"{0}\", \"w\") ; "
                        "file_text = base64.decodestring({1}) ; "
                        "file_handle.write(file_text) ; "
                        "file_handle.close()'").format(output_file, b64_str)
                    user_data_list[user_data_list.index(line)] = decode_cmd


def _replace_user_data(a_struct):
    """ Take scripts from the user-data-scripts folder and JSON-ify
        them and insert them into UserData if UserData contains
        the key/value pair:  "ReplaceWithFile" : "files/FILE_NAME"
    """
    for resource_name, resource in a_struct["Resources"].items():
        if resource["Type"] == "AWS::EC2::Instance" and \
        "UserData" in resource["Properties"]:
            user_data = resource["Properties"]["UserData"]
            if len(user_data) == 1 and \
            "ReplaceWithFile" in user_data:
                resource["Properties"]["UserData"] = _ravel_user_data(
                    user_data["ReplaceWithFile"])


def _ravel_user_data(a_file_name):
    """ Read a file in the user-data-scripts folder and convert it
        to a JSON-able data structure that can be assigned to
        resource["UserData"]
    """
    file_name = "{0}/{1}".format(USER_DATA_DIR, a_file_name)
    if not os.path.isfile(file_name):
        print("Error:  {0} does not exist".format(a_file_name))
        exit(1)
    with open(file_name) as file_handle:
        file_text = file_handle.read()
    file_lines = file_text.split("\n")
    line_list = [x for y in file_lines for x in (y, "\n")]
    return {"Fn::Base64":{"Fn::Join":["", line_list]}}


def assemble(a_dir):
    """ Assemble a directory of JSON snippets into a monolithic
        JSON file
    """
    if not os.path.isdir(a_dir):
        print("Error, {0} does not exist".format(a_dir))
        exit(1)
    full_path = os.path.abspath(a_dir)
    base_name = os.path.basename(full_path)
    file_name = base_name if not base_name.endswith(".d") \
        else base_name.rsplit(".")[0]
    output_file = "{0}.json".format(file_name)

    root_file = "{0}/ROOT.json".format(full_path)

    if not os.path.isfile(root_file):
        print("Error:  ROOT.json does not exist")
        exit(1)

    with open(root_file) as file_handle:
        try:
            root_data = json.load(
                file_handle, object_pairs_hook=collections.OrderedDict)
        except Exception as ex:
            print("Error parsing ROOT.json as JSON data: {0}".format(ex))
            exit(1)

    order_list = root_data["Resources"]
    file_list = [x for x in os.listdir(full_path) if x != "ROOT.json"]

    if sorted(order_list) != sorted(file_list):
        print("Error:  File list does not match the list in ROOT.json")
        print("\n".join(difflib.unified_diff(
            sorted(order_list), sorted(file_list), "ROOT.json", "Files")))
        exit(1)

    new_resources = collections.OrderedDict()

    for file_name in order_list:
        file_path = "{0}/{1}".format(full_path, file_name)
        with open(file_path) as file_handle:
            try:
                new_resources[file_name] = json.load(
                    file_handle, object_pairs_hook=collections.OrderedDict)
            except Exception as ex:
                print("Error loading {0}:  {1}".format(file_name, ex))
                exit(1)

    root_data["Resources"] = new_resources

    _replace_user_data(root_data)
    _embed_files(root_data)

    with open(output_file, "w") as file_handle:
        json.dump(root_data, file_handle, separators=(",", ":"), indent=4)

    print("File created at {0}".format(output_file))

def fetch_template(a_region, a_stack_name):
    """ Get the Cloudformation template used to launch a running
        stack and store it locally.  Based on code shamelessly stolen
        from abutcher.
    """
    try:
        import boto
        import boto.cloudformation

        conn = boto.cloudformation.connect_to_region(a_region)
        stack = conn.describe_stacks(stack_name_or_id=a_stack_name)
        template = stack[0].get_template()
        template_body = template['GetTemplateResponse'][
            'GetTemplateResult']['TemplateBody']
        template_body = _purify_json(template_body)
        file_name = "STACK-{0}-{1}-{2}.json".format(
            a_region, a_stack_name,
            datetime.datetime.now().strftime('%Y-%m-%d_%H-%M'))
        with open(file_name, "w") as file_handle:
            file_handle.write(template_body)
        print("Wrote existing stack to {0}".format(file_name))
        return file_name
    except Exception as ex:
        print("Error:  Could not fetch template, please ensure that you "
            "have the latest version of Boto installed from Pip, the "
            "version available in Yum may be outdated")
        print(ex)
        exit(1)

def diff_stack(a_file, a_region, a_stack_name):
    """ Diff an assembled template with a running stack """
    if not os.path.isfile(a_file):
        print("{0} does not exist".format(a_file))
        exit(1)
    stack_file = fetch_template(a_region, a_stack_name)
    json_diff(stack_file, a_file)

def list_stacks(a_region):
    """ List all running stacks in the account associated with
        the current AWS credentials
    """
    try:
        import boto
        import boto.cloudformation

        conn = boto.cloudformation.connect_to_region(a_region)
        stack_list = [x for x in conn.list_stacks()
            if x.stack_status != "DELETE_COMPLETE"]
        max_len = max([len(x.stack_name) for x in stack_list])
        stack_list.sort(key=lambda x: x.stack_name)
        for stack in stack_list:
            print("{0}/{1}{2} : {3}".format(
                a_region, stack.stack_name,
                " " * (max_len - len(stack.stack_name)),
                stack.stack_status))
    except Exception as ex:
        print("Error:  Could not fetch template, please ensure that you "
            "have the latest version of Boto installed from Pip, the "
            "version available in Yum may be outdated")
        print(ex)
        exit(1)


def disassemble(a_file):
    """ Disassemble a monolithic JSON file into a folder by
        splitting the "Resources" section into individual files.
    """
    dir_name = a_file if not a_file.lower().endswith(".json") \
        else a_file.rsplit(".", 1)[0]

    dir_name += ".d"

    if not os.path.isdir(dir_name):
        os.mkdir(dir_name)
    else:
        if os.listdir(dir_name):
            os.system("rm -f {0}/*".format(dir_name))

    with open(a_file) as file_handle:
        try:
            json_data = json.load(
                file_handle, object_pairs_hook=collections.OrderedDict)
        except Exception as ex:
            print("Error:  {0} is not valid JSON.  {1}".format(a_file, ex))
            exit(1)

    new_resources = []

    for k, v in json_data["Resources"].items():
        with open("{0}/{1}".format(dir_name, k), "w") as file_handle:
            json.dump(v, file_handle, separators=(",", ":"), indent=4)
            new_resources.append(k)

    json_data["Resources"] = new_resources

    with open("{0}/ROOT.json".format(dir_name), "w") as file_handle:
        json.dump(json_data, file_handle, separators=(",", ":"), indent=4)

def clean_folder(a_folder):
    """ Force all files in a folder to the standard JSON formatting
        this script uses.
    """
    if not os.path.isdir(a_folder):
        print("Error:  {0} does not exist".format(a_folder))
        exit(1)
    result = {}
    for file_name in os.listdir(a_folder):
        file_path = "{0}/{1}".format(a_folder, file_name)
        with open(file_path) as file_handle:
            try:
                result[file_path] = _purify_json(
                    file_handle.read(), a_preserve_order=True)
            except Exception as ex:
                print(ex)
                print("Error loading {0}".format(file_path))
    for k, v in result.items():
        with open(k, "w") as file_handle:
            file_handle.write(v)

def clean_file(a_file):
    """ Apply standardized formatting (indentation, etc...) to
        a JSON file.
    """
    if not os.path.isfile(a_file):
        print("Error:  {0} does not exist".format(a_file))
        exit(1)

    with open(a_file) as file_handle:
        try:
            result = _purify_json(file_handle.read(), a_preserve_order=True)
        except Exception as ex:
            print(ex)
            print("Error loading {0}".format(a_file))

    with open(a_file, "w") as file_handle:
        file_handle.write(result)


def main():
    """ Parse CLI args and execute """
    import optparse

    parser = optparse.OptionParser(
        "%prog [options]",
        description="Simple CLI tool to automate splitting and "
        "un-splitting Cloudformation templates and related functions")

    parser.add_option(
        "--assemble", "-a", dest="assemble_dir",
        default=None, help="Assemble the file from the snippets "
        "in ASSEMBLE_DIR")

    parser.add_option(
        "--disassemble", "-d", dest="disassemble_file",
        default=None, help="Disassemble DISASSEMBLE_FILE into a directory "
        "of snippets")

    parser.add_option(
        "--diff", dest="diff",
        default=None, help="Diff 2 JSON files.  "
        "Format:  file1.json/file2.json")

    parser.add_option(
        "--diff-stack", dest="diff_stack",
        default=None, help="Diff a JSON file with a running stack.  "
        "Format:  file.json/region/stack_name")

    parser.add_option(
        "--list-stacks", dest="stack_region", default=None,
        help="List stacks in STACK_REGION.  "
        "You must export "
        "the environment variables AWS_ACCESS_KEY_ID and "
        "AWS_SECRET_ACCESS_KEY to connect to AWS.")

    parser.add_option(
        "--fetch", dest="stack_name", default=None,
        help="Fetch an existing stack template from AWS and store locally."
        "  Format for STACK_NAME:  region/stack_name.  You must export "
        "the environment variables AWS_ACCESS_KEY_ID and "
        "AWS_SECRET_ACCESS_KEY to connect to AWS.")

    parser.add_option(
        "--clean-file", dest="file",
        default=None, help="Ensure that FILE has consistently formatted, "
        "diff-able JSON")

    parser.add_option(
        "--clean-folder", dest="folder",
        default=None, help="Ensure that all files in FOLDER have "
        " consistently formatted, diff-able JSON")

    (options, args) = parser.parse_args()

    option_list = [x for x in
        (options.assemble_dir, options.disassemble_file, options.diff,
         options.stack_name, options.folder, options.file,
         options.stack_region, options.diff_stack) if x]

    if len(option_list) > 1:
        print("Error:  You can only specify one argument")
        exit(1)

    if options.assemble_dir:
        assemble(options.assemble_dir)
    elif options.disassemble_file:
        disassemble(options.disassemble_file)
    elif options.stack_region:
        list_stacks(options.stack_region)
    elif options.stack_name:
        arg_list = [x.strip() for x in options.stack_name.split("/")]
        if len(arg_list) != 2:
            print("Error:  Format is region/stack_name")
            exit(1)
        fetch_template(arg_list[0], arg_list[1])
    elif options.diff:
        diff_list = [x.strip() for x in options.diff.split("/")]
        if len(diff_list) != 2:
            print("Error:  Format is file1.json/file2.json")
            exit(1)
        json_diff(diff_list[0], diff_list[1])
    elif options.diff_stack:
        diff_list = [x.strip() for x in options.diff_stack.split("/")]
        if len(diff_list) != 3:
            print("Error:  Format is file.json/region/stack_name")
            exit(1)
        diff_stack(diff_list[0], diff_list[1], diff_list[2])
    elif options.folder:
        clean_folder(options.folder)
    elif options.file:
        clean_file(options.file)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
