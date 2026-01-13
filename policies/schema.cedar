entity User {}
entity Document {}

action "read" appliesTo { principal: User, resource: Document }
action "write" appliesTo { principal: User, resource: Document }
action "delete" appliesTo { principal: User, resource: Document }
