use std::io::{self, Read};

use cedar_policy::Policy;

fn main() {
    let mut input = String::new();
    if io::stdin().read_to_string(&mut input).is_err() {
        eprintln!("failed to read policy from stdin");
        std::process::exit(2);
    }
    let policy_src = input.trim();
    if policy_src.is_empty() {
        eprintln!("policy input was empty");
        std::process::exit(3);
    }

    let policy = match Policy::parse(None, policy_src) {
        Ok(policy) => policy,
        Err(err) => {
            eprintln!("failed to parse policy: {err}");
            std::process::exit(1);
        }
    };

    let json = match policy.to_json() {
        Ok(json) => json,
        Err(err) => {
            eprintln!("failed to serialize policy to json: {err}");
            std::process::exit(4);
        }
    };

    match serde_json::to_string(&json) {
        Ok(output) => {
            println!("{output}");
        }
        Err(err) => {
            eprintln!("failed to encode policy json: {err}");
            std::process::exit(5);
        }
    }
}
