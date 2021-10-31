#! /usr/bin/awk -f

# This awk script adds Github annotations to the end of the output when
# critical or error messages are detected in pytest output.
#
#     pytest -v -s --log-cli-level=DEBUG | ./ci/annotate.awk

/CRITICAL/ { critical_msg[critical_count++] = $0 }
/ERROR/    { error_msg[error_count++] = $0 }
1          # print every line

END { 
    print ""
    if (critical_count > 0) {
        printf "::error title=annotate.awk::%d critical messages in %s\n", critical_count, ENVIRON["BUILD_NAME"]
        for (i in critical_msg) {
            print critical_msg[i]
        }
    }

    print ""
    if (error_count > 0) {
        printf "::warning title=annotate.awk::%d error messages in %s\n", error_count, ENVIRON["BUILD_NAME"]
        for (i in error_msg) {
            print error_msg[i]
        }
    }
}
