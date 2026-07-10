







import argparse
import json

def main():
    parser = argparse.ArgumentParser(prog="kwin-strip-effect-metadata")
    parser.add_argument("--source", help="input file", required=True)
    parser.add_argument("--output", help="output file", required=True)
    args = parser.parse_args()
    stripped_json = dict(KPlugin=dict())
    with open(args.source, "r") as src:
        original_json = json.load(src)
        stripped_json["KPlugin"]["EnabledByDefault"] = original_json["KPlugin"]["EnabledByDefault"]

    with open(args.output, "w") as dst:
        json.dump(stripped_json, dst)


if __name__ == "__main__":
    main()
