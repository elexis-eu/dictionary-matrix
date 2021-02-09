#!/usr/bin/env python3
import re
import sys

if __name__ == '__main__':
    file1, file2 = sys.argv[1:3]
    text1 = open(file1).read()
    text2 = open(file2).read()
    sense_id1 = re.search(r':sense <(.+?)>', text1).group(1)  # type: ignore
    sense_id2 = re.search(r':sense <(.+?)>', text2).group(1)  # type: ignore
    print(f'<{file1}#{sense_id1}> '
          f'<http://www.w3.org/2004/02/skos/core#exactMatch> '
          f'<{file2}#{sense_id2}> . # 0.8000')
