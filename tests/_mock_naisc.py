#!/usr/bin/env python3
import re
import sys

if __name__ == '__main__':
    file1, file2 = sys.argv[len(sys.argv) - 2:]
    text1 = open(file1).read()
    text2 = open(file2).read()
    # Find 'cat-n-1' sense in both texts
    sense_id1 = re.search(r':sense <(.+?-n-1)>', text1).group(1)  # type: ignore
    sense_id2 = re.search(r':sense <(.+?-n-1)>', text2).group(1)  # type: ignore
    print(f'<{file1}#{sense_id1}> '
          f'<http://www.w3.org/2004/02/skos/core#exactMatch> '
          f'<{file2}#{sense_id2}> . # 0.8000')
