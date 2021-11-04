#! /usr/bin/awk -f

# This awk script adds Github annotations to the end of the output when
# critical or error messages are detected in pytest output.
#
#     pytest -v -s --log-cli-level=DEBUG | ./ci/annotate.awk

/CRITICAL/ { critical_msg[critical_count++] = $0 }
/ERROR/    { if (!match($0, /test_logger/)) error_msg[error_count++] = $0 }
/WARNING/  { warning_msg[warning_count++] = $0 }
1          # print every line

END { 
    if (critical_count > 0) {
        # Github "error" message.
        printf "\n::error title=Critical Messages::%d critical messages in %s\n", critical_count, ENVIRON["BUILD_NAME"]
        for (i in critical_msg) {
            print critical_msg[i]
        }
    }

    if (error_count > 0) {
        # Github "warning" message.
        printf "\n::warning title=Error Messages::%d error messages in %s\n", error_count, ENVIRON["BUILD_NAME"]
        for (i in error_msg) {
            print error_msg[i]
        }
    }

    if (warning_count > 0) {
        # Github "notice" message.
        printf "\n::notice title=Warning Messages::%d warning messages in %s\n", warning_count, ENVIRON["BUILD_NAME"]
        for (i in warning_msg) {
            print warning_msg[i]
        }
    }
}
