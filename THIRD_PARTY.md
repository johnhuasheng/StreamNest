# Third-party components in the Windows portable package

StreamNest's own source code is licensed under MIT; see `LICENSE`.

The portable ZIP includes Node.js and npm dependencies. Node.js has its own
license text in `runtime/Node-LICENSE.txt`. Each package in `node_modules`
retains its package metadata and applicable license files.

The portable ZIP also includes a separate FFmpeg command-line executable from
the Gyan Windows build. That build reports GPL v3. Its license, build README,
and the corresponding FFmpeg source commit archive are included as
`runtime/FFmpeg-LICENSE.txt`, `runtime/FFmpeg-README.txt`, and
`runtime/FFmpeg-source.zip`. StreamNest invokes the executable as a separate
process. FFmpeg is not covered by StreamNest's MIT license.

See https://ffmpeg.org/legal.html and https://nodejs.org/ for upstream project
information. The exact bundled versions are recorded in the accompanying
README and executable metadata.
