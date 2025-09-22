import argparse
import os
import sys

import toml


def __parse_arguments(argv=None):
    """Parses the arguments passed into the script"""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target", type=str, help="Which package.toml to use for this rsync script", default="docker_build"
    )

    parser.add_argument(
        "--output-folder",
        type=str,
        help="Which folder to rsync artifacts into.  Defaults to _build/packages/output/",
        default="_build/packages/output/",
    )

    parser.add_argument(
        "--platform",
        type=str,
        help="Which platform to use. Defaults to linux-x86_64",
        default="linux-x86_64",
    )

    parser.add_argument(
        "--hardcode-pip-prebundle-links",
        action="store_true",
        default=False,
        help="This will setup a symlink to handle pip_prebundles for docker builds.",
    )

    parser.add_argument(
        "--extra-exclude-list", type=str, help="Set this to add an extra exclude file to the rsync script", default=""
    )

    return parser.parse_args(argv)


def main(argv=None):

    arguments = __parse_arguments(argv)

    target = arguments.target
    output_folder = arguments.output_folder
    hardcode_pip_prebundle_links = arguments.hardcode_pip_prebundle_links

    package_paths = []
    exclude_paths = []
    files_strip = []
    files_strip_exclude = []

    try:

        with open("docker_package.toml") as pack_toml_file:
            pack_toml = toml.load(pack_toml_file)
            if target in pack_toml:
                target_toml = pack_toml[target]

                if "files" in target_toml:
                    package_paths.extend(target_toml["files"])
                if "files_exclude" in target_toml:
                    exclude_paths.extend(target_toml["files_exclude"])
                if "files_strip" in target_toml:
                    files_strip.extend(target_toml["files_strip"])
                if "files_strip_exclude" in target_toml:
                    files_strip_exclude.extend(target_toml["files_strip_exclude"])

                if f"{arguments.platform}" in target_toml:
                    target_toml = target_toml[f"{arguments.platform}"]
                    if "files" in target_toml:
                        package_paths.extend(target_toml["files"])
                    if "files_exclude" in target_toml:
                        exclude_paths.extend(target_toml["files_exclude"])
                    if "files_strip" in target_toml:
                        files_strip.extend(target_toml["files_strip"])
                    if "files_strip_exclude" in target_toml:
                        files_strip_exclude.extend(target_toml["files_strip_exclude"])
    except FileNotFoundError:
        print("ERROR: docker_package.toml not found", file=sys.stderr)
        sys.exit(1)
    except toml.TomlDecodeError as e:
        print(f"ERROR: Invalid TOML in docker_package.toml: {e}", file=sys.stderr)
        sys.exit(1)

    rename_list = []

    exclude_paths = [
        x[0].replace("${config}", "release").replace("${platform}", f"{arguments.platform}") for x in exclude_paths
    ]

    # Done gathering data, time to do things with it
    if len(exclude_paths):
        with open("./exclude_list.txt", "w") as out_file:
            for path in exclude_paths:
                out_file.write(path + "\n")

    with open("./generated_rsync_package.sh", "w") as out_file:
        out_file.write(f"output_folder={output_folder}\n\n")
        out_file.write(f"config=release\nplatform={arguments.platform}\n")
        out_file.write("mkdir -p ${output_folder}\n\n")
        out_file.write("\necho Starting rsync, please wait...\n")
        exclude_flag = ""
        if hardcode_pip_prebundle_links:
            out_file.write(
                f'find _build/{arguments.platform}/release -type l -iname "pip_prebundle" > pip_prebundle_locations.txt\n\n'
            )
            exclude_flag += "--exclude-from=./pip_prebundle_locations.txt"
        if len(exclude_paths):
            exclude_flag += " --exclude-from=./exclude_list.txt"
        if len(arguments.extra_exclude_list):
            exclude_flag += f" --exclude-from={arguments.extra_exclude_list}"

        multi_source_str = " ".join([x[0] for x in package_paths if len(x) == 1])
        out_file.write(f"rsync -a -R -K --copy-unsafe-links {exclude_flag} {multi_source_str}" + " ${output_folder}\n")

        for package_path in package_paths:
            if len(package_path) == 2:
                out_file.write(f"\nmkdir -p ${{output_folder}}/{package_path[1]}\n")
                out_file.write(
                    f"rsync -a -K --copy-unsafe-links {exclude_flag} {package_path[0]}"
                    + " ${output_folder}/"
                    + f"{package_path[1]}\n"
                )

        if len(rename_list):
            out_file.write("\n#Renaming files\n")
            for rename_paths in rename_list:
                out_file.write(
                    "mv ${output_folder}"
                    f"{rename_paths[0].replace('$platform', arguments.platform)} "
                    "${output_folder}"
                    f"{rename_paths[1].replace('$platform', arguments.platform)}\n"
                )
            out_file.write("\n")

        for file_strip_array in files_strip:
            for file_strip in file_strip_array:
                out_file.write("\n#Stripping files\n")
                excludes = ""
                if len(files_strip_exclude):
                    # create a find command to strip files not including anything that matches files_strip_exclude
                    excludes = " ".join(
                        [f"-not -path '{file_strip_exclude[0]}' " for file_strip_exclude in files_strip_exclude]
                    )
                out_file.write(
                    f"find ${{output_folder}} -type f -ipath '{file_strip}' {excludes} -exec strip {{}} \\;\n"
                )

        if hardcode_pip_prebundle_links:
            out_file.write("\n#Setup hardcoded symlinks for pip_prebundle")
            out_file.write(
                """
for location in $(cat pip_prebundle_locations.txt); do
    ln -s /drivesim-ov/_build/target-deps/pip_prebundle ${output_folder}${location}
done\n"""
            )
    os.chmod("./generated_rsync_package.sh", 0o755)


if __name__ == "__main__":
    sys.exit(main())
