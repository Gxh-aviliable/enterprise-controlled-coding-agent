# Open Files in VSCode Design

## Goal

Let users open workspace files from the web UI in either local desktop VSCode or a server-hosted Web VSCode instance.

## Modes

- `local-vscode`: backend returns a `vscode://file/...` URL for the authenticated user's workspace root, plus a `file_url` field for the selected file.
- `web-vscode`: backend returns a configured browser URL for `code-server`, `openvscode-server`, or a compatible Web VSCode deployment.

## Configuration

- `FILE_OPEN_MODE=local-vscode`
- `VSCODE_WEB_BASE_URL=`
- `VSCODE_WEB_URL_TEMPLATE=`
- `VSCODE_WORKSPACE_PATH=`

`VSCODE_WORKSPACE_PATH` is treated as a multi-user base path by default, so `/srv/workspaces` becomes `/srv/workspaces/user_<id>` for the authenticated user. If the value contains placeholders, it is used as a template and can include `{user_id}` or `{workspace_name}`.

If `VSCODE_WEB_URL_TEMPLATE` is set, it can use `{path}`, `{workspace}`, `{relative_path}`, `{user_id}`, and `{workspace_name}` placeholders. If it is not set, the backend builds a URL from `VSCODE_WEB_BASE_URL` using `folder` and `file` query parameters.

## API

`GET /workspace/open-url?path=<relative file path>` returns:

```json
{
  "mode": "local-vscode",
  "url": "vscode://file/D:/.../workspace/user_1",
  "file_url": "vscode://file/D:/.../workspace/user_1/file.py"
}
```

The route uses existing workspace path resolution and only returns URLs for files inside the authenticated user's workspace.

## Frontend

- Add an "Open workspace in VSCode" icon button to `FileViewer`.
- Add "Open workspace" to file context menus in `TreeNode`.
- The frontend opens the returned URL in a new tab/window.

## Testing

- Unit tests cover local VSCode URL generation, Web VSCode template generation, default Web VSCode URL generation, and directory rejection.
- Frontend build verifies the UI compiles.
