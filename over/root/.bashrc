# The live image uses an overlay root; only the installed system should call marvin.
case "$(findmnt -n -o FSTYPE / 2>/dev/null)" in
  "" | overlay) echo install vix ;;
  *) echo call marvin ;;
esac
