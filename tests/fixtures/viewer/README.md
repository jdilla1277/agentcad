# Historical saved viewer

`legacy_viewer.html` is the unchanged `_HTML_UNIFIED` template from public
commit `289d0e557f52c8ef0bd00cafe60031815aa257a4`, the parent of `5fd2fa81`.
It predates the live viewer's ready/capture/restore message protocol.

The browser regression fills its original placeholders with a real, saved
multi-part bracket GLB. Keeping the historical template here makes the upgrade
case reproducible without Git history, a network checkout, or rebuilding an old
package environment. Tests must not add live messages to this fixture.

Source: https://github.com/jdilla1277/agentcad/blob/289d0e557f52c8ef0bd00cafe60031815aa257a4/src/agentcad/commands/view.py

The template is covered by this repository's Apache-2.0 license. It contains
no customer model, credentials, absolute paths or private project data.
