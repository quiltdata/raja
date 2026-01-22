use std::env;
use std::fs;
use std::path::Path;

use cedar_policy::PolicySet;
use glob::glob;

fn main() {
    let policy_dir = env::args().nth(1).unwrap_or_else(|| "policies".to_string());
    let policy_root = Path::new(&policy_dir);
    if !policy_root.is_dir() {
        eprintln!("policy directory not found: {}", policy_root.display());
        std::process::exit(2);
    }

    let mut combined = String::new();
    let pattern = policy_root.join("*.cedar");
    let pattern_str = pattern
        .to_str()
        .expect("policy directory path should be valid utf-8");

    for entry in glob(pattern_str).expect("failed to read policy glob pattern") {
        let path = match entry {
            Ok(path) => path,
            Err(err) => {
                eprintln!("failed to resolve policy file: {err}");
                std::process::exit(3);
            }
        };
        if path.file_name().and_then(|name| name.to_str()) == Some("schema.cedar") {
            continue;
        }
        let content = fs::read_to_string(&path)
            .unwrap_or_else(|err| panic!("failed to read {}: {err}", path.display()));
        combined.push_str(&content);
        if !content.ends_with('\n') {
            combined.push('\n');
        }
    }

    if combined.trim().is_empty() {
        eprintln!("no policies found in {}", policy_root.display());
        std::process::exit(4);
    }

    if let Err(err) = combined.parse::<PolicySet>() {
        eprintln!("failed to parse policies with cedar-policy: {err}");
        std::process::exit(1);
    }
}
