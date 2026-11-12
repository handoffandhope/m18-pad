# Gives the M18 panel (and the session keys' Haiku faces) a long-lived Claude login in Windows Credential Manager.
# Run it by hand on the PC: it opens the Claude sign-in, then asks you to paste the token it shows.
# -Print writes the stored token to stdout and nothing else.
param([switch]$Print, [string]$Target = 'Claude/M18/OAuth')
$ErrorActionPreference = 'Stop'

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class M18Cred {
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
  struct CREDENTIAL {
    public uint Flags; public uint Type; public string TargetName; public string Comment;
    public long LastWritten; public uint CredentialBlobSize; public IntPtr CredentialBlob;
    public uint Persist; public uint AttributeCount; public IntPtr Attributes;
    public string TargetAlias; public string UserName;
  }
  [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
  static extern bool CredWriteW(ref CREDENTIAL credential, uint flags);
  [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
  static extern bool CredReadW(string target, uint type, uint flags, out IntPtr credential);
  [DllImport("advapi32.dll")]
  static extern void CredFree(IntPtr buffer);
  public static void Write(string target, string secret) {
    byte[] bytes = Encoding.Unicode.GetBytes(secret);
    IntPtr blob = Marshal.AllocHGlobal(bytes.Length);
    try {
      Marshal.Copy(bytes, 0, blob, bytes.Length);
      CREDENTIAL credential = new CREDENTIAL { Type = 1, TargetName = target, UserName = "m18", Persist = 2, CredentialBlobSize = (uint)bytes.Length, CredentialBlob = blob };
      if (!CredWriteW(ref credential, 0)) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
    } finally { Marshal.FreeHGlobal(blob); }
  }
  public static string Read(string target) {
    IntPtr pointer;
    if (!CredReadW(target, 1, 0, out pointer)) return "";
    try {
      CREDENTIAL credential = (CREDENTIAL)Marshal.PtrToStructure(pointer, typeof(CREDENTIAL));
      return Marshal.PtrToStringUni(credential.CredentialBlob, (int)credential.CredentialBlobSize / 2);
    } finally { CredFree(pointer); }
  }
}
'@

function Save-M18Token([string]$Token, [string]$Name) {
  $clean = $Token -replace '\s', ''  # a long token pasted from a terminal can carry line breaks
  if ($clean -notmatch '^sk-ant-') { throw "That doesn't look like a Claude token (they start with sk-ant-). Nothing was saved." }
  [M18Cred]::Write($Name, $clean)
}

if ($MyInvocation.InvocationName -eq '.') { return }  # the check script loads the functions only
if ($Print) { [Console]::Out.Write([M18Cred]::Read($Target)); return }

$report = Join-Path $env:TEMP 'm18-panel-login.txt'
foreach ($name in 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SSE_PORT', 'CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_BASE_URL') { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
try {
  Write-Host 'Step 1 of 2: sign in to Claude in the browser that opens.'
  & claude setup-token
  Write-Host ''
  Write-Host 'Step 2 of 2: copy the token shown above (it starts with sk-ant-), paste it here and press Enter. It stays hidden.'
  $secure = Read-Host 'Token' -AsSecureString
  Save-M18Token ([System.Net.NetworkCredential]::new('', $secure).Password) $Target

  Write-Host 'Saved. Checking it works...'
  $env:CLAUDE_CODE_OAUTH_TOKEN = [M18Cred]::Read($Target)
  $reply = & claude -p 'Reply with just the word OK' --strict-mcp-config 2>&1 | Out-String
  Remove-Item Env:CLAUDE_CODE_OAUTH_TOKEN
  if ($reply -notmatch 'OK') { throw "Claude didn't accept the token: $($reply.Trim())" }
  Set-Content -LiteralPath $report -Value "OK $(Get-Date -Format s)"
  Write-Host 'The M18 panel is signed in. Ask it something on http://127.0.0.1:8725.'
} catch {
  Set-Content -LiteralPath $report -Value "FAILED $(Get-Date -Format s): $($_.Exception.Message)"
  Write-Host "Failed: $($_.Exception.Message)"
}
